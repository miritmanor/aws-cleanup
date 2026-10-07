"""How old a resource is, and whether that counts as stale. Base tier because
collectors judge staleness with evidence only they hold."""

from datetime import timezone

from .activity import (
    LOOKUP_FAILED as ACTIVITY_LOOKUP_FAILED,
    NO_DATAPOINTS as ACTIVITY_NO_DATA,
    OBSERVED_ACTIVITY as ACTIVITY_OBSERVED,
    OBSERVED_ZERO as ACTIVITY_ZERO,
)
from .config import STALE_THRESHOLD_DAYS, now


# The three answers to "was this used", as countable values; the flag column is
# the sentence. Evidence about use only - never a recommendation.
USAGE_UNUSED = "unused"
USAGE_ACTIVE = "active"
USAGE_UNKNOWN = "unknown"

# Every flag starts with one of these words, so the derivation is total (tested).
_USAGE_BY_PREFIX = {
    "STALE": USAGE_UNUSED,
    "ACTIVE": USAGE_ACTIVE,
    "UNKNOWN": USAGE_UNKNOWN,
}


def usage_state(flag):
    """The flag column's verdict as one of three values. Derived from the flag, so
    collector-specific judgements are kept and the two cannot disagree."""
    # A malformed flag classifies as "unknown"; it never aborts a scan.
    words = flag.split(None, 1) if isinstance(flag, str) else []
    return _USAGE_BY_PREFIX.get(words[0] if words else "", USAGE_UNKNOWN)


def days_ago(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now() - dt).days


def flag_stale(last_signal_days, created_days, inferred):
    """Return a simple human flag for the summary column."""
    reference = last_signal_days if last_signal_days is not None else created_days
    if reference is None:
        return "UNKNOWN"
    if reference >= STALE_THRESHOLD_DAYS:
        return "STALE (INFERRED)" if inferred else "STALE"
    return "ACTIVE (INFERRED)" if inferred else "ACTIVE"


def flag_from_activity(evidence, created_days):
    """The flag column from activity evidence: observed use -> ACTIVE; measured zero
    -> STALE; no datapoints -> per METRIC_ABSENCE_MEANS_IDLE; unavailable -> UNKNOWN."""
    from .config import METRIC_ABSENCE_MEANS_IDLE

    if evidence is None:
        return "UNKNOWN"
    if evidence.telemetry == ACTIVITY_OBSERVED:
        days = days_ago(evidence.last_activity)
        return flag_stale(days, created_days, False)
    if evidence.telemetry == ACTIVITY_ZERO:
        return (f"STALE (no activity in {evidence.window_days}d)"
                if evidence.window_days >= STALE_THRESHOLD_DAYS
                else f"UNKNOWN (no activity in {evidence.window_days}d, "
                     f"less than the {STALE_THRESHOLD_DAYS}d staleness window)")
    if evidence.telemetry == ACTIVITY_NO_DATA:
        if METRIC_ABSENCE_MEANS_IDLE.get(evidence.metric):
            return f"STALE (no {evidence.metric} events in {evidence.window_days}d)"
        return "UNKNOWN (metric published no datapoints; it may not be enabled)"
    if evidence.telemetry == ACTIVITY_LOOKUP_FAILED:
        return "UNKNOWN (activity lookup failed - see notes)"
    return "UNKNOWN (no usage telemetry exists for this resource type)"
