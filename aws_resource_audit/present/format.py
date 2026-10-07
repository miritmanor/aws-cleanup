"""Turning values into the strings a human reads. The only place that does this."""

from datetime import timezone

from ..rows import ROW_FIELDS
from .cost import cost_columns
from .references import render_connections


def fmt_dt(dt):
    """Format any datetime as 'YYYY-MM-DD HH:MM:SS UTC' - deliberately not ISO, so
    spreadsheets treat every date column the same way."""
    if dt is None or dt == "":
        return ""
    if isinstance(dt, str):
        # Raise rather than pass through: formatting before render destroys the value.
        raise TypeError(
            f"expected a datetime, got the already-formatted string {dt!r}. "
            "Collectors store datetimes; present/format.py renders them.")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def tags_to_str(tags_dict):
    """Flatten tags for a single spreadsheet cell. The dict on the row is the
    real value; this is one rendering of it."""
    if not tags_dict:
        return ""
    return "; ".join(f"{k}={v}" for k, v in sorted(tags_dict.items()))


def display_row(row, extra=None):
    """A row as text: the public schema with typed fields rendered. Every writer goes
    through this, so rows keep values until the edge."""
    out = {field: row[field] for field in ROW_FIELDS}
    out["created"] = fmt_dt(row["created"])
    out["last_used"] = fmt_dt(row["last_used"])
    out["tags"] = tags_to_str(row["tags"])
    out["activity"] = activity_to_str(row.get("activity"))
    # Derived from row["cost"] here, so re-wording never needs a billing request.
    out.update(cost_columns(row))
    out["connections"] = render_connections(row)
    if extra:
        out.update(extra)
    return out


# How each telemetry state reads in a cell; must be legible on its own.
_ACTIVITY_WORDING = {
    "observed_activity": "used (observed)",
    "observed_zero": "no activity (measured)",
    "no_datapoints": "no datapoints",
    "unavailable": "no telemetry for this type",
    "lookup_failed": "activity lookup FAILED",
}


def activity_to_str(evidence):
    """One cell's worth of "how do we know", from the evidence dict."""
    if not evidence:
        return ""
    said = _ACTIVITY_WORDING.get(evidence.get("telemetry"), evidence.get("telemetry", ""))
    metric = evidence.get("metric")
    window = evidence.get("window_days")
    detail = ", ".join(part for part in (
        metric,
        f"{window}d window" if window else "",
    ) if part)
    return f"{said} [{detail}]" if detail else said
