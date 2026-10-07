"""Matching recorded references against rows that exist: resolved, dangling or silent.
Recomputes risk afterwards. No AWS calls."""

from .. import coverage as cov
from ..config import ARN_PREFIX_MAX_MATCHES, FILTERED_ENUMERATIONS
from ..registry import CONNECTION_TYPES_BY_ID, feeds_grouping
from .risk import compute_risk

# A reference that states no scope of its own is resolved under its connection
# type's rule; see ConnectionType.target_scope.
ANY_SCOPE = "*"


def _in_scope(row, edge, source_row):
    """Is this row a legitimate target by account and region? Stated account else the
    source's; stated region, "*" or the scope rule; "global" rows are in every region."""
    want_account = edge.get("target_account") or source_row.get("account") or ""
    row_account = row.get("account") or ""
    # An unknown account is not a different one; otherwise old snapshots lose every link.
    if want_account and row_account and want_account != ANY_SCOPE:
        if row_account != want_account:
            return False

    if row.get("region") == "global":
        return True

    want_region = edge.get("target_region")
    if want_region == ANY_SCOPE:
        return True
    if want_region is None:
        conn = CONNECTION_TYPES_BY_ID.get(edge["conn_type"])
        if conn is not None and conn.target_scope == "account":
            return True
        # A global source has no region, so a regional rule means account-wide.
        if source_row.get("region") == "global":
            return True
        want_region = source_row.get("region")
    return row.get("region") == want_region


# Why a reference resolved to nothing. Only a confirmed miss may lower removal risk;
# the others mean nobody looked.
OUT_OF_SCOPE = "out_of_scope"                      # never in this scan's scope
LOOKUP_FAILED = "lookup_failed"                    # denied, failed or partial
NOT_FOUND_IN_INVENTORY = "not_found_in_inventory"  # looked everywhere, absent
CONFIRMED_MISSING = "confirmed_missing"            # a complete listing lacked it

# The one state that is evidence of deletion. The others are evidence of
# nothing, and must never make a resource look safer to remove.
_PROVES_ABSENCE = frozenset({CONFIRMED_MISSING})

_GAP_WORDING = {
    OUT_OF_SCOPE: "the target is outside the scanned regions/accounts",
    LOOKUP_FAILED: "the target's own lookup failed or was denied",
    NOT_FOUND_IN_INVENTORY: "the target was not found, and nothing confirms it is gone",
}


def _unresolved_state(edge, source_row, coverage, target_service):
    """Which kind of "not found" this is. Out of scope is checked first: it depends on
    the reference, not on what the scan saw."""
    want_account = edge.get("target_account")
    if (want_account and want_account != ANY_SCOPE
            and want_account != (source_row.get("account") or want_account)):
        return OUT_OF_SCOPE

    if coverage is None or not target_service:
        # No ledger (old snapshot, unit test): say only "not in the inventory".
        return NOT_FOUND_IN_INVENTORY

    scope = _effective_target_scope(edge, source_row)
    status = coverage.status(target_service, scope)
    if status == cov.NOT_REQUESTED:
        return OUT_OF_SCOPE
    if status in cov.INCOMPLETE_STATUSES:
        return LOOKUP_FAILED
    if status == cov.COMPLETE and _absence_is_provable(
            coverage, target_service, scope):
        # Fully listed and absent: the only state that may make removal look safer.
        return CONFIRMED_MISSING
    return NOT_FOUND_IN_INVENTORY


def _effective_target_scope(edge, source_row):
    """The one scope this reference points into, or None for anywhere. A regional
    link from a regional source means that source's region."""
    stated = edge.get("target_region")
    if stated == ANY_SCOPE:
        return None
    if stated:
        return stated
    conn = CONNECTION_TYPES_BY_ID.get(edge["conn_type"])
    if conn is not None and conn.target_scope == "account":
        return None
    source_region = source_row.get("region")
    return None if source_region == "global" else source_region


def _absence_is_provable(coverage, target_service, scope):
    """Can a complete listing prove absence? Only if it is exhaustive (not filtered) and
    covers everywhere the target could be."""
    if target_service in FILTERED_ENUMERATIONS:
        return False
    if scope is not None:
        return True
    return coverage.scopes_for(target_service) == {"global"}


def _evidence_gap(states):
    """One phrase naming what is unknown, for the risk rating to quote."""
    for state in (LOOKUP_FAILED, OUT_OF_SCOPE, NOT_FOUND_IN_INVENTORY):
        if state in states:
            return _GAP_WORDING[state]
    return ""


def _widened_deliberately(edge):
    """True when the reference itself asks for every match, so several
    matches are the correct answer rather than an ambiguity to refuse."""
    return (edge.get("target_region") == ANY_SCOPE
            or edge.get("target_account") == ANY_SCOPE
            or edge["target_match"] == "prefix")


def resolve_edges(all_rows, coverage=None):
    """Resolve every edge against existing rows, annotate both ends' connections, write
    row["_graph"], recompute risk. Returns (source, target, reason) links for grouping."""
    idx_by_id = {}
    idx_by_name = {}
    idx_by_arn = {}
    for idx, r in enumerate(all_rows):
        if r.get("resource_id"):
            idx_by_id.setdefault(r["resource_id"], []).append(idx)
        if r.get("name"):
            idx_by_name.setdefault(r["name"], []).append(idx)
        if r.get("arn"):
            idx_by_arn.setdefault(r["arn"], []).append(idx)

    links = []
    outgoing = {}   # source idx -> reference records, in the order found
    incoming = {}   # target idx -> reference records
    dangling = set()
    resolved_src = set()
    unresolved_states = {}   # source idx -> the states its misses landed in
    explained_later = []     # (idx, dangling record, state, conn_type that may explain it)

    for idx, row in enumerate(all_rows):
        for edge in row.get("_edges", []):
            by_prefix = edge["target_match"] == "prefix"
            # An exact ARN is tried first; most types have none, so a miss falls through.
            exact = [t for t in idx_by_arn.get(edge.get("target_arn") or "", [])
                     if t != idx]
            if exact:
                raw_targets = exact
            elif by_prefix:
                raw_targets = [t for rid, idxs in idx_by_id.items()
                               if rid.startswith(edge["target_id"])
                               for t in idxs if t != idx]
            else:
                table = idx_by_name if edge["target_match"] == "name" else idx_by_id
                raw_targets = [t for t in table.get(edge["target_id"], []) if t != idx]
            # Scope filters BEFORE the type guard, so other regions are not called collisions.
            if not exact:
                raw_targets = [t for t in raw_targets
                               if _in_scope(all_rows[t], edge, row)]
            target_service = edge.get("target_service")
            type_mismatches = []
            if target_service:
                targets = [t for t in raw_targets if all_rows[t]["service"] == target_service]
                type_mismatches = [t for t in raw_targets if all_rows[t]["service"] != target_service]
                if type_mismatches and not targets and not by_prefix:
                    # Matched, but not the expected type: a coincidental collision, recorded so
                    # it is visible. Not for prefix matches, which sweep other types normally.
                    outgoing.setdefault(idx, []).append({
                        "kind": "collision",
                        "target_id": edge["target_id"],
                        "expected_service": target_service,
                        "matched": [all_rows[t]["service"] for t in type_mismatches],
                        "rel": edge["rel"], "evidence": edge["evidence"],
                        "confidence": edge["confidence"],
                        "conn_type": edge["conn_type"], "grouping": False,
                    })
            else:
                targets = raw_targets
            if by_prefix and len(targets) > ARN_PREFIX_MAX_MATCHES:
                # Too broad: a naming convention, not a dependency. Recorded, not silent.
                outgoing.setdefault(idx, []).append({
                    "kind": "prefix-refused",
                    "target_id": edge["target_id"],
                    "target_service": target_service,
                    "match_count": len(targets), "limit": ARN_PREFIX_MAX_MATCHES,
                    "rel": edge["rel"], "evidence": edge["evidence"],
                    "confidence": edge["confidence"],
                    "conn_type": edge["conn_type"], "grouping": False,
                })
                targets = []
            # Captured now: CONNECTION_STATES can be swapped later (tests do).
            groups = feeds_grouping(edge["conn_type"])
            if len(targets) > 1 and not _widened_deliberately(edge):
                # Several equally good matches: linking all would invent dependencies,
                # picking one would be a coin toss. Recorded as ambiguous.
                outgoing.setdefault(idx, []).append({
                    "kind": "ambiguous",
                    "target_id": edge["target_id"],
                    "target_service": target_service,
                    "candidates": sorted(all_rows[t]["resource_key"]
                                         for t in targets),
                    "rel": edge["rel"], "evidence": edge["evidence"],
                    "confidence": edge["confidence"],
                    "conn_type": edge["conn_type"],
                    "target_qualifier": edge.get("target_qualifier"),
                    "grouping": False,
                })
                continue
            if targets:
                for tidx in targets:
                    tgt, src = all_rows[tidx], all_rows[idx]
                    # A prefix edge's target_id is the pattern; show the matched row's own id.
                    shown_id = tgt["resource_id"] if by_prefix else edge["target_id"]
                    common = {
                        "rel": edge["rel"], "evidence": edge["evidence"],
                        "confidence": edge["confidence"],
                        "conn_type": edge["conn_type"], "grouping": groups,
                        "target_qualifier": edge.get("target_qualifier"),
                    }
                    outgoing.setdefault(idx, []).append(dict(
                        common, kind="resolved", target_idx=tidx,
                        target_id=shown_id, target_service=tgt["service"]))
                    incoming.setdefault(tidx, []).append(dict(
                        common, kind="incoming", source_idx=idx,
                        source_id=src["resource_id"], source_service=src["service"]))
                    if groups:
                        # The why_grouped reason: its own prose, separate from the connections column.
                        conf = ("" if edge["confidence"] == "authoritative"
                                else f", {edge['confidence']}")
                        links.append((
                            idx, tidx,
                            f"{src['service']}:{src['resource_id']} {edge['rel']} "
                            f"{tgt['service']}:{shown_id} (via {edge['evidence']}{conf})",
                            # Grouping asks what KIND of link this is, so the type travels with it.
                            edge["conn_type"]))
                    # A real connection for risk, whether or not it may cluster.
                    resolved_src.add(idx)
            elif edge["assert_exists"] and not (target_service and type_mismatches):
                # A type mismatch means the target exists: a collision, not dangling.
                state = _unresolved_state(edge, row, coverage, target_service)
                record = {
                    "kind": "dangling",
                    # WHY it is unresolved; the only field risk may act on.
                    "state": state,
                    "target_idx": None,
                    "target_id": edge["target_id"],
                    "target_service": target_service,
                    "target_account": edge.get("target_account"),
                    "target_region": edge.get("target_region"),
                    "rel": edge["rel"], "evidence": edge["evidence"],
                    "confidence": edge["confidence"],
                    "conn_type": edge["conn_type"],
                    # A target outside the scan cannot have grouped anything.
                    "grouping": False,
                }
                explained_later.append((idx, record, state, edge.get("superseded_by")))

    # Decided after every edge, since the link that explains a miss may come later in the row.
    for idx, record, state, other in explained_later:
        if other and any(r["kind"] == "resolved" and r["conn_type"] == other
                         and r["target_id"] == record["target_id"] for r in outgoing.get(idx, [])):
            continue
        outgoing.setdefault(idx, []).append(record)
        unresolved_states.setdefault(idx, set()).add(state)
        dangling.add(idx)

    for idx, row in enumerate(all_rows):
        row["_references"] = outgoing.get(idx, []) + incoming.get(idx, [])
        row["_has_resolved_link"] = idx in resolved_src or idx in incoming
        row["_has_dangling"] = idx in dangling
        states = unresolved_states.get(idx, set())
        # Empty only when every miss was confirmed: the one case risk may fall to LOW.
        row["_evidence_gap"] = _evidence_gap(states - _PROVES_ABSENCE)

    # Risk once every reference is resolved. "Has connections" = collector prose or any reference.
    for row in all_rows:
        if row["flag"] == "ERROR":
            continue
        has_dangling = row["_has_dangling"]
        row["risk_if_removed"] = compute_risk(
            row["flag"],
            bool(row.get("connections") or row["_references"])
            and not (has_dangling and not row["_has_resolved_link"]),
            row.get("_cost_hint"),
            has_dangling=has_dangling,
            evidence_gap=row["_evidence_gap"],
        )
    return links
