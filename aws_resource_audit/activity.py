"""Whether a resource was used, and how well that is known: used, measured
idle, or no signal at all - three answers, never collapsed into one."""

from dataclasses import dataclass, field
from datetime import datetime

# Where the evidence came from.
SOURCE_CLOUDWATCH = "cloudwatch"
SOURCE_API = "api"          # a real last-used field, e.g. IAM RoleLastUsed
SOURCE_CONFIG = "config"    # a modification time - NOT use; see below
SOURCE_STATE = "state"      # attachment or lifecycle state, e.g. "in-use"
SOURCE_NONE = "none"        # no source exists for this resource type

# What the source said.
OBSERVED_ACTIVITY = "observed_activity"  # positive samples: it was used
OBSERVED_ZERO = "observed_zero"          # samples exist, all zero: it was not
NO_DATAPOINTS = "no_datapoints"          # the metric published nothing
UNAVAILABLE = "unavailable"              # no such metric for this type at all
LOOKUP_FAILED = "lookup_failed"          # denied, throttled, broken

# The telemetry states that settle the question. The rest mean "unknown", and
# a report may not turn any of them into a verdict about use.
_CONCLUSIVE = frozenset({OBSERVED_ACTIVITY, OBSERVED_ZERO})


@dataclass(frozen=True)
class ActivityEvidence:
    """One answer about one resource's use, with its provenance attached."""

    source: str = SOURCE_NONE
    metric: str = ""
    telemetry: str = UNAVAILABLE
    # The window the claim was measured over travels with the claim.
    window_days: int = 0
    # Set ONLY from a positive observation, never from a zero-valued sample.
    last_activity: datetime = None
    # The newest sample of any value. Evidence that telemetry exists at all,
    # which is different from evidence of use.
    latest_sample: datetime = None
    explanation: str = ""
    error: str = ""

    @property
    def used(self):
        return self.telemetry == OBSERVED_ACTIVITY

    @property
    def is_known(self):
        """True when the evidence settles the question either way."""
        return self.telemetry in _CONCLUSIVE

    def as_dict(self):
        data = dict(self.__dict__)
        for name in ("last_activity", "latest_sample"):
            stamp = data.get(name)
            data[name] = stamp.isoformat() if hasattr(stamp, "isoformat") else None
        return data


def unavailable(explanation, source=SOURCE_NONE):
    """For a type AWS publishes no usage signal for (EBS snapshot, AMI, ...).
    Says so outright instead of letting creation age pass for use."""
    return ActivityEvidence(source=source, telemetry=UNAVAILABLE,
                            explanation=explanation)


def from_timestamp(when, *, source, metric="", explanation=""):
    """For a real last-used field AWS maintains - IAM's RoleLastUsed, an
    access key's LastUsedDate. These are observations, not proxies."""
    if when:
        return ActivityEvidence(source=source, metric=metric,
                                telemetry=OBSERVED_ACTIVITY, last_activity=when,
                                latest_sample=when, explanation=explanation)
    return ActivityEvidence(source=source, metric=metric,
                            telemetry=NO_DATAPOINTS, explanation=explanation)


def configuration_change(when, *, explanation):
    """A modification time: recorded and labelled, never counted as use."""
    return ActivityEvidence(source=SOURCE_CONFIG, telemetry=UNAVAILABLE,
                            latest_sample=when, explanation=explanation)


def best_of(*evidence):
    """The strongest answer among several metrics: used > measured idle > no data
    > unavailable. A failed lookup never outranks a real observation."""
    candidates = [e for e in evidence if e is not None]
    if not candidates:
        return ActivityEvidence()
    order = {OBSERVED_ACTIVITY: 0, OBSERVED_ZERO: 1, NO_DATAPOINTS: 2,
             LOOKUP_FAILED: 3, UNAVAILABLE: 4}
    best = min(candidates, key=lambda e: (order.get(e.telemetry, 9),
                                          -(e.last_activity.timestamp()
                                            if e.last_activity else 0)))
    metrics = "/".join(dict.fromkeys(e.metric for e in candidates if e.metric))
    return ActivityEvidence(
        source=best.source, metric=metrics or best.metric,
        telemetry=best.telemetry, window_days=best.window_days,
        last_activity=best.last_activity, latest_sample=best.latest_sample,
        explanation=best.explanation, error=best.error)


def restore(data):
    """An ActivityEvidence back from its serialized form."""
    if not data:
        return ActivityEvidence()
    fields = dict(data)
    for name in ("last_activity", "latest_sample"):
        value = fields.get(name)
        fields[name] = datetime.fromisoformat(value) if value else None
    known = {f for f in ActivityEvidence.__dataclass_fields__}
    return ActivityEvidence(**{k: v for k, v in fields.items() if k in known})
