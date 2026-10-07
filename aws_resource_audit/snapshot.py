"""The scan snapshot that separates scanning from rendering. Rows are stored raw and
selected by SNAPSHOT_ROW_FIELDS; the graph is stored uncontracted."""

import logging
import json
import os
from dataclasses import dataclass, field
from datetime import datetime

from .errors import AuditError
from .activity import ActivityEvidence
from .billing import CostAttribution, UNAVAILABLE, not_queried, restore_billing
from .rows import ROW_FIELDS, resource_key
from .scope import DEFAULT_PARTITION
from .staleness import usage_state

logger = logging.getLogger(__name__)

# Bumped when an older file would render wrongly. ANY ADDITION TO ROW_FIELDS BUMPS
# THIS (display_row reads every field); add an _upgrade_ step for older files.
SCHEMA_VERSION = 10

# The public schema plus the one internal field the renderers genuinely need.
SNAPSHOT_ROW_FIELDS = ROW_FIELDS + ("_references", "_detected_project_id",
                                    "_detected_project_group", "_detected_grouping_method")

# Stored as ISO 8601; values in, values out, text only at the edge.
_DATETIME_FIELDS = ("created", "last_used")


def _upgrade_2_to_3(data):
    """Version 3 added arn, account, partition and resource_key. The account is
    recovered from the file's top level; nothing else is guessed."""
    account = data.get("account") or ""
    for row in data.get("rows") or []:
        row.setdefault("arn", "")
        row.setdefault("account", account)
        row.setdefault("partition", DEFAULT_PARTITION)
        row.setdefault("resource_key", resource_key(
            row.get("service", ""), row.get("region", ""),
            row.get("resource_id", ""), account, DEFAULT_PARTITION))
    return data


# from_version -> upgrade to the next version, applied in sequence. What cannot be
# reconstructed is left unknown, never invented.
def _upgrade_3_to_4(data):
    """Version 4 added the coverage ledger; an old file gets an empty one ("unknown")."""
    data.setdefault("coverage", [])
    return data


def _upgrade_4_to_5(data):
    """Version 5 added activity evidence; old rows are marked unavailable, not reconstructed."""
    for row in data.get("rows") or []:
        row.setdefault("activity", ActivityEvidence().as_dict())
    return data


def _upgrade_5_to_6(data):
    """Version 6 added the membership model; the old label is kept, the rest unknown."""
    for row in data.get("rows") or []:
        row.setdefault("project_id", "")
        row.setdefault("membership", "unassigned" if not row.get("project_group") else "assigned")
        row.setdefault("shared_with", [])
        row.setdefault("project_candidates", [])
        row.setdefault("environment", "")
        row.setdefault("component", "")
    return data


def _upgrade_6_to_7(data):
    """Version 7 replaced the cost number with an attribution. The old even-split
    figure cannot be upgraded, so it is marked unavailable and billing not queried."""
    data.setdefault("billing", not_queried().as_dict())
    stale = CostAttribution(
        state=UNAVAILABLE,
        explanation=("Written by a scan that divided an account-wide service "
                     "total evenly across the rows it happened to hold. That "
                     "figure cannot be checked against a region or against what "
                     "the scan could see, so it is not shown. Re-scan for a "
                     "cost attribution.")).as_dict()
    for row in data.get("rows") or []:
        row.setdefault("cost", stale)
    return data


def _upgrade_7_to_8(data):
    """Version 8 added a discovery pass, since removed; kept so the chain still runs."""
    return data


def _upgrade_8_to_9(data):
    """Version 9 added usage_state, re-derived exactly from the stored flag."""
    for row in data.get("rows") or []:
        row.setdefault("usage_state", usage_state(row.get("flag")))
    return data


# What why_tier said on a default verdict before version 10.
_LEGACY_DEFAULT_WHY_TIER = ("no rule found evidence this is deployment-only machinery, "
                            "so it is assumed to be part of the running application")


def _upgrade_9_to_10(data):
    """Version 10 blanks the default-verdict why_tier sentence older scans stored."""
    for row in data.get("rows") or []:
        if row.get("why_tier") == _LEGACY_DEFAULT_WHY_TIER:
            row["why_tier"] = ""
    return data


_UPGRADES = {
    2: _upgrade_2_to_3,
    3: _upgrade_3_to_4,
    4: _upgrade_4_to_5,
    5: _upgrade_5_to_6,
    6: _upgrade_6_to_7,
    7: _upgrade_7_to_8,
    8: _upgrade_8_to_9,
    9: _upgrade_9_to_10,
}


@dataclass
class Snapshot:
    """One scan, as read back from disk."""

    rows: list = field(default_factory=list)
    graph: dict = field(default_factory=dict)
    grouping: dict = field(default_factory=dict)
    coverage: list = field(default_factory=list)
    # Stored because the request is chargeable and the renderer never calls AWS.
    # Defaults to "not queried", never zero.
    billing: object = field(default_factory=not_queried)
    account: str = ""
    regions: list = field(default_factory=list)
    # When the SCAN ran, not when it was read. The report is stamped with this,
    # so a page rendered a week later still says when the data is from.
    scanned_at: datetime = None


def _dump_datetime(value):
    """Datetimes out as ISO, anything falsy (None or "") out as null."""
    if not value:
        return None
    return value.isoformat()


def _load_datetime(value):
    if not value:
        return None
    return datetime.fromisoformat(value)


def snapshot_row(row):
    """One row, reduced to what the file may contain."""
    out = {name: row[name] for name in SNAPSHOT_ROW_FIELDS}
    for name in _DATETIME_FIELDS:
        out[name] = _dump_datetime(row[name])
    return out


def restore_row(row):
    """Undo snapshot_row's date handling, in place. Required: fmt_dt() raises on
    an already-formatted string."""
    for name in _DATETIME_FIELDS:
        row[name] = _load_datetime(row.get(name))
    row.setdefault("_references", [])
    # A file older than this field was written before saved groups set
    # project_id, so the stored project_id is still the scan's own.
    row.setdefault("_detected_project_id", row.get("project_id") or "")
    # Older snapshots: keep the current name, and treat "named" as "inferred".
    row.setdefault("_detected_project_group", row.get("project_group") or "")
    method = row.get("grouping_method") or ""
    row.setdefault("_detected_grouping_method", "inferred" if method == "named" else method)
    return row


def build_snapshot(all_rows, graph, grouping, account, regions, scanned_at,
                   coverage=None, billing=None):
    return {
        "schema_version": SCHEMA_VERSION,
        "scanned_at": _dump_datetime(scanned_at),
        "account": account or "",
        "regions": list(regions or []),
        # Stored so an offline render can still tell "none found" from "not allowed".
        "coverage": list(coverage or []),
        # The billing response, so the rendering side can explain and total
        # figures it must never re-fetch.
        "billing": (billing if billing is not None else not_queried()).as_dict(),
        # Only the notes the grouping audit reads; see the module docstring on
        # why grouping_links is not among them.
        "grouping": {
            "conflicts": (grouping or {}).get("conflicts") or [],
            "tag_conflicts": (grouping or {}).get("tag_conflicts") or [],
            "shared": (grouping or {}).get("shared") or [],
            "candidate_words": (grouping or {}).get("candidate_words") or [],
        },
        "graph": graph or {},
        # Last, and the only large key: a file whose head is readable tells you
        # which scan it is without paging through fifty thousand rows.
        "rows": [snapshot_row(row) for row in all_rows],
    }


def write_snapshot(path, all_rows, graph, grouping, account, regions, scanned_at,
                   coverage=None, billing=None):
    """Write the snapshot atomically (temp file in the same directory, then rename).
    A failed write removes the temp file: it is a full map of the account."""
    payload = build_snapshot(all_rows, graph, grouping, account, regions,
                             scanned_at, coverage=coverage, billing=billing)
    directory = os.path.dirname(path) or "."
    temp = os.path.join(directory, f".{os.path.basename(path)}.partial")
    try:
        with open(temp, "w") as handle:
            json.dump(payload, handle, indent=2, default=str)
    except BaseException:
        # BaseException, not Exception: an interrupted scan must not leave the
        # account map behind either.
        try:
            os.unlink(temp)
        except OSError as e:
            # Worth saying out loud: what is left behind is a full account map.
            logger.warning("could not remove the partial snapshot %s (%s) - it "
                           "holds account data; delete it by hand", temp, e)
        raise
    os.replace(temp, path)


def read_snapshot(path):
    """Read a snapshot, or raise AuditError naming the file and saying to run a scan."""
    if not os.path.exists(path):
        raise AuditError(
            f"{path}: no scan snapshot here. Run aws_resource_audit.py first - "
            "it writes the snapshot this renders from. If your scan wrote "
            "somewhere else, check "
            "'output_dir' and 'snapshot_file' in audit_config.json.")
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError) as e:
        raise AuditError(f"{path}: could not be read ({e})")
    if not isinstance(data, dict):
        raise AuditError(f"{path}: expected a JSON object at the top level")

    version = data.get("schema_version")
    while version != SCHEMA_VERSION and version in _UPGRADES:
        data = _UPGRADES[version](data)
        version += 1
        data["schema_version"] = version
    if version != SCHEMA_VERSION:
        raise AuditError(
            f"{path}: snapshot schema version {version!r}, this build reads "
            f"{SCHEMA_VERSION} and knows no way to upgrade it. Re-run "
            "aws_resource_audit.py to write a current one.")

    rows = data.get("rows")
    if not isinstance(rows, list):
        raise AuditError(f"{path}: 'rows' is missing or is not a list")

    return Snapshot(
        rows=[restore_row(row) for row in rows],
        graph=data.get("graph") or {},
        grouping=data.get("grouping") or {},
        coverage=data.get("coverage") or [],
        billing=restore_billing(data.get("billing")),
        account=data.get("account") or "",
        regions=data.get("regions") or [],
        scanned_at=_load_datetime(data.get("scanned_at")),
    )
