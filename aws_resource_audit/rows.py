"""The row schema: one scanned resource is one dict with the ROW_FIELDS keys.
new_row() builds it, add_edge() records a reference, member_key() is its stable identity."""

import logging

from .config import SERVICE_BILLING
from .connection_types.vocabulary import CONFIDENCE_DISPLAY
from .registry import CONNECTION_TYPES_BY_ID, detection_enabled
from .activity import ActivityEvidence
from .billing import CostAttribution
from .scope import scan_account, scan_partition
from .staleness import usage_state


def _unknown_cost():
    """Default cost for a row: NOT_QUERIED, never zero - a blank reads as free."""
    return CostAttribution().as_dict()


def _unknown_activity():
    """Default activity: UNAVAILABLE, so a reader is told there is no telemetry."""
    return ActivityEvidence().as_dict()

logger = logging.getLogger(__name__)

def error_row(service, region, resource_id, message):
    """Stand-in row for a failed collector call, built through new_row() so no
    column comes out blank."""
    # The one place every failed lookup passes through, whichever collector
    # built it - so this is the one place that has to log it.
    logger.warning("lookup failed: %s %s %s - %s",
                   service, region, resource_id, message)
    row = new_row(service, region, resource_id, "", "", "", None, True,
                  "ERROR", message)
    # "We could not look" differs from "we looked and the signals were weak".
    row["risk_if_removed"] = "UNKNOWN - lookup failed, see notes"
    return row



# Edges: every link is stored on the SOURCE row with the evidence for it; resolve_edges()
# matches it to a real row and describes it on both sides. Fields: see add_edge's arguments.

def add_edge(row, target_id, rel, evidence, *, conn_type, confidence=None,
             assert_exists=True, target_match="id", target_service=None,
             target_arn=None, target_account=None, target_region=None,
             target_qualifier=None, superseded_by=None):
    """`conn_type` is required so every link can be switched off. Set `target_service`
    when target_id is a user-chosen name; "prefix" matching needs it and is capped."""
    # Validated before the target_id check so a typo'd id always raises,
    # regardless of whether this particular resource happened to have a target.
    if not detection_enabled(conn_type):
        return
    if not target_id:
        return
    if confidence is None:
        confidence = CONFIDENCE_DISPLAY[CONNECTION_TYPES_BY_ID[conn_type].confidence]
    row.setdefault("_edges", []).append({
        "target_id": target_id, "rel": rel, "evidence": evidence,
        "confidence": confidence, "assert_exists": assert_exists,
        "target_match": target_match, "target_service": target_service,
        "conn_type": conn_type,
        # Region name = that region, "*" = deliberately every region (a policy wildcard),
        # None = not stated, so the connection type's scope rule decides.
        "target_arn": target_arn,
        "target_account": target_account,
        "target_region": target_region,
        # Kept as evidence: an alias pinned to a version reads different env vars than $LATEST.
        "target_qualifier": target_qualifier,
        "superseded_by": superseded_by,
    })


def account_from_arn(arn):
    """The account an ARN names, or "". Deliberately not a full parse."""
    if not arn or not arn.startswith("arn:"):
        return ""
    parts = arn.split(":", 5)
    if len(parts) < 6:
        return ""
    account = parts[4]
    return "" if account in ("", "*") else account


def partition_from_arn(arn):
    if not arn or not arn.startswith("arn:"):
        return ""
    parts = arn.split(":", 5)
    return parts[1] if len(parts) >= 6 and parts[1] != "*" else ""


def resource_key(service, region, resource_id, account="", partition=""):
    """A resource's canonical identity: partition, internal type, account, scope, native
    id. Never the display name - names are not unique."""
    return ":".join((
        partition or scan_partition(),
        service,
        account or scan_account(),
        region,
        resource_id or "",
    ))


def new_row(service, region, resource_id, name, created, last_used,
            last_used_days, inferred, flag, notes, tags=None, description="",
            connections="", cost_hint=None, group_key=None, vpc_id=None,
            iam_role=None, arn="", account=None, activity=None,
            aws_default=False):
    """Build a row with the full schema. Datetimes and the tags dict stay as values
    until present/ renders them; "_" fields are internal and never written out."""
    tags = tags or {}
    # An ARN's account outranks the scan's own: a scan can find another account's
    # resource (a shared AMI, a sample bucket).
    account = account or account_from_arn(arn) or scan_account()
    partition = partition_from_arn(arn) or scan_partition()
    return {
        "service": service, "region": region, "resource_id": resource_id,
        # Identity. `arn` is "" where AWS exposes none; resource_key is always set.
        "arn": arn,
        "account": account,
        "partition": partition,
        "resource_key": resource_key(service, region, resource_id,
                                     account, partition),
        # How use was established, or why it could not be; the flag summarises this.
        "activity": (activity.as_dict() if activity is not None
                     else _unknown_activity()),
        "name": name, "created": created, "last_used": last_used,
        "last_used_days": last_used_days, "inferred": inferred, "flag": flag,
        # The flag's verdict as a countable value, derived here so the two never disagree.
        "usage_state": usage_state(flag),
        "notes": notes,
        "tags": tags,
        "description": description,
        "connections": connections,
        # Derived centrally so every collector stays consistent - see SERVICE_BILLING.
        "billing": SERVICE_BILLING.get(service, ("unknown", ""))[0],
        "billing_note": SERVICE_BILLING.get(service, ("", "billing model for this type not classified"))[1],
        # Filled by resolve_edges once every reference is matched; error_row() sets its own.
        "risk_if_removed": "",
        # A structured attribution; the two text columns beside it are derived at
        # render time, so re-wording never needs another Cost Explorer request.
        "cost": _unknown_cost(),
        "est_monthly_cost_usd": "", "cost_notes": "",
        "project_group": "", "grouping_method": "", "why_grouped": "",
        # project_group is only a display label; filter and aggregate on project_id.
        "project_id": "",
        "membership": "",
        "shared_with": [],
        "project_candidates": [],
        # Recorded, never merged on: dev and prod can belong to one application.
        "environment": "", "component": "",
        # Runtime or deployment, filled by apply_tiers. "" means no evidence either way,
        # which is not the same claim as "runtime".
        "tier": "", "why_tier": "",
        # Only what the collector stated; tag fallbacks are decided by apply_project_grouping.
        "_group_key": group_key,
        "_vpc": vpc_id,
        "_role": iam_role,
        "_cost_hint": cost_hint,
        # True for a piece of a default VPC that AWS created itself. Read by
        # analyze/default_network.py, which hides an untouched one.
        "_aws_default": aws_default,
        "_edges": [],
        # Every reference, resolved by resolve_edges; the graph, audit and connections
        # column all read this one list.
        "_references": [],
        # What the grouping pass detected, before any saved name claimed the row.
        # reset_assigned_names restores all three.
        "_detected_project_id": "",
        "_detected_project_group": "",
        "_detected_grouping_method": "",
    }
# The public schema, in output order. Writers SELECT these fields, so internal
# "_" fields can never leak and nothing has to be stripped.
ROW_FIELDS = (
    "service", "region", "resource_id",
    "arn", "account", "partition", "resource_key",
    "activity",
    "name", "created",
    "last_used", "last_used_days", "inferred", "flag", "usage_state",
    "notes", "tags",
    "description", "connections", "billing", "billing_note", "risk_if_removed",
    "est_monthly_cost_usd", "cost", "cost_notes", "project_group", "project_id",
    "membership", "shared_with", "project_candidates", "environment",
    "component", "grouping_method",
    "why_grouped", "tier", "why_tier",
)


def member_key(row):
    """Stable identity for a resource across runs, independent of any
    grouping label. Used as the unit of overlap-matching below."""
    return f"{row['service']}:{row['region']}:{row['resource_id']}"
