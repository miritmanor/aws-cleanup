"""The CSV inventory: the column list and display_row() pass, shared by every caller."""

import csv
import io

from .format import display_row

# The columns, and their order. Asserted against ROW_FIELDS by tests/test_json.py.
CSV_FIELDNAMES = [
    "service", "region", "resource_id",
    # Identity: filter or join on resource_key; arn is empty for many types.
    "arn", "account", "partition", "resource_key",
    "name", "created",
    "last_used", "last_used_days", "inferred", "flag", "usage_state",
    "activity", "billing", "billing_note",
    # The figure, then what kind of figure it is, then why - read in that order.
    "est_monthly_cost_usd", "cost", "cost_notes", "risk_if_removed",
    "connections", "why_grouped", "project_group", "project_id", "membership",
    "shared_with", "project_candidates", "environment", "component",
    "grouping_method",
    "tier", "why_tier", "notes", "tags", "description",
]


def render_csv(all_rows, *, lineterminator="\r\n"):
    """The inventory as CSV text. lineterminator is a parameter for the golden test."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDNAMES,
                            extrasaction="ignore", lineterminator=lineterminator)
    writer.writeheader()
    writer.writerows(display_row(row) for row in all_rows)
    return buffer.getvalue()


def write_csv(all_rows, path):
    with open(path, "w", newline="") as handle:
        handle.write(render_csv(all_rows))
