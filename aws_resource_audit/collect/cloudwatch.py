"""CloudWatch metric lookups as activity evidence, and the notes sentence
that says how use was established."""

from datetime import timedelta

from .. import activity, coverage
from ..config import CW_LOOKBACK_DAYS, now
from .calls import brief_error, safe_call


def cw_activity(cw, namespace, metric_name, dimensions, stat="Sum",
                explanation=""):
    """Activity evidence from one CloudWatch metric. An all-zero series is
    OBSERVED_ZERO with no last_activity - never a use."""
    end = now()
    start = end - timedelta(days=CW_LOOKBACK_DAYS)
    resp = safe_call(
        cw.get_metric_statistics,
        capability=coverage.METRICS,
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=dimensions,
        StartTime=start,
        EndTime=end,
        Period=86400,  # daily
        Statistics=[stat],
    )
    common = dict(source=activity.SOURCE_CLOUDWATCH, metric=metric_name,
                  window_days=CW_LOOKBACK_DAYS,
                  explanation=explanation or f"{metric_name} over {CW_LOOKBACK_DAYS}d")
    if "__error__" in resp:
        return activity.ActivityEvidence(
            telemetry=activity.LOOKUP_FAILED, error=resp["__error__"], **common)
    points = sorted(resp.get("Datapoints") or [], key=lambda d: d["Timestamp"])
    if not points:
        return activity.ActivityEvidence(telemetry=activity.NO_DATAPOINTS, **common)
    nonzero = [p for p in points if (p.get(stat) or 0) > 0]
    if nonzero:
        return activity.ActivityEvidence(
            telemetry=activity.OBSERVED_ACTIVITY,
            last_activity=nonzero[-1]["Timestamp"],
            latest_sample=points[-1]["Timestamp"], **common)
    return activity.ActivityEvidence(
        telemetry=activity.OBSERVED_ZERO,
        latest_sample=points[-1]["Timestamp"], **common)


def cw_note(base_note, cw_error):
    if cw_error:
        return f"Could not read CloudWatch metric ({cw_error}); falling back to creation date"
    return base_note


def activity_note(evidence, base_note=""):
    """The notes column's sentence about how use was established."""
    said = {
        activity.OBSERVED_ACTIVITY:
            f"{evidence.metric} recorded activity in the last {evidence.window_days}d.",
        activity.OBSERVED_ZERO:
            f"{evidence.metric} was published and recorded ZERO activity across "
            f"the whole {evidence.window_days}d window - measured, not assumed.",
        activity.NO_DATAPOINTS:
            f"{evidence.metric} published no datapoints in {evidence.window_days}d.",
        activity.LOOKUP_FAILED:
            f"Could not read {evidence.metric} ({brief_error(evidence.error)}) - "
            "activity is UNKNOWN here, not idle.",
        activity.UNAVAILABLE:
            "AWS publishes no usage signal for this resource type, so its "
            "activity is unknown - age is not a substitute.",
    }.get(evidence.telemetry, "")
    return " ".join(part for part in (said, base_note) if part)
