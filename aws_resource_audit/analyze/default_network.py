"""Hides a default VPC exactly as AWS created it: only default pieces, nothing linked
to them, and every lookup in the region complete. Runs first in analysis."""

from ..coverage import INCOMPLETE_STATUSES, INVENTORY

# The types AWS creates as part of a default VPC.
DEFAULT_NETWORK_TYPES = frozenset({"VPC", "Subnet", "RouteTable",
                                   "InternetGateway", "SecurityGroup"})


def _aws_created(row):
    """Is this row a piece of the default VPC as AWS made it? The internet gateway
    attached to a default VPC counts as one."""
    service = row.get("service")
    if service == "InternetGateway":
        return True
    return service in DEFAULT_NETWORK_TYPES and bool(row.get("_aws_default"))


def _regions_with_gaps(coverage_entries):
    return {e.get("scope") for e in coverage_entries or ()
            if e.get("capability") == INVENTORY
            and e.get("status") in INCOMPLETE_STATUSES}


def hide_untouched_default_networks(rows, default_vpc_ids, coverage_entries=None,
                                    known=True):
    """Remove untouched default VPCs and their pieces from `rows` in place; return the
    regions affected, sorted."""
    if not known or not default_vpc_ids:
        return []
    gaps = _regions_with_gaps(coverage_entries)
    members = {}
    for row in rows:
        if row.get("_vpc") in default_vpc_ids:
            members.setdefault(row["_vpc"], []).append(row)

    hidden_rows, regions = set(), []
    for vpc_id, pieces in members.items():
        vpc = next((r for r in pieces if r.get("service") == "VPC"
                    and r.get("resource_id") == vpc_id), None)
        if vpc is None or vpc.get("region") in gaps:
            continue
        if not all(_aws_created(r) for r in pieces):
            continue
        inside = {id(r) for r in pieces}
        ids = {r.get("resource_id") for r in pieces}
        pointed_at = any(edge.get("target_id") in ids
                         for r in rows
                         if id(r) not in inside and r.get("region") == vpc.get("region")
                         for edge in r.get("_edges") or ())
        if pointed_at:
            continue
        hidden_rows |= inside
        regions.append(vpc.get("region"))

    if hidden_rows:
        rows[:] = [r for r in rows if id(r) not in hidden_rows]
    return sorted(regions)
