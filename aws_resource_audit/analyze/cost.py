"""Attributing the bill to rows, or refusing to. A bucket is split evenly only when its
region's rows exist, it has no declared gaps and every billing type was fully scanned."""

from decimal import ROUND_DOWN, Decimal

from ..billing import (
    ESTIMATED, NOT_QUERIED, SHARED_UNALLOCATED, UNAVAILABLE, ZERO,
    BucketTotal, CostReport, attribution_for,
)
from ..config import BILLING_COVERAGE, CE_SERVICE_MAP, USAGE_TYPE_RESOURCE_TYPES
from ..coverage import INCOMPLETE_STATUSES, NOT_REQUESTED, WORST_FIRST

CENT = Decimal("0.01")

# Statuses meaning the scan did not see everything the money could belong to.
# A type with no ledger entry is not one of these: it means "none found".
_INCOMPLETE = INCOMPLETE_STATUSES | {NOT_REQUESTED}


# The region collectors give a resource that has none. Cost Explorer gives
# such a charge no region at all, which collect/cost.py stores as "".
ACCOUNT_WIDE = "global"


def _bill_region(row):
    """The region a row's charge appears under on the bill."""
    region = row.get("region") or ""
    return "" if region == ACCOUNT_WIDE else region


def ce_services_for(service):
    """The Cost Explorer bucket(s) a resource type bills into."""
    direct = CE_SERVICE_MAP.get(service, ())
    if direct:
        return direct
    return tuple(sorted(name for name, (collected, _gaps) in BILLING_COVERAGE.items()
                        if service in collected))


def _internal_services(ce_service):
    """Every resource type declared as billing into one bucket."""
    return tuple(BILLING_COVERAGE.get(ce_service, ((), ()))[0])


def _region_status(entries, service, region):
    """The worst inventory status recorded for one type in one scope."""
    seen = [e.get("status") for e in entries
            if e.get("service") == service and e.get("capability") == "inventory"
            and e.get("scope") in (region, "global")]
    if not seen:
        return "unknown"
    for status in WORST_FIRST:
        if status in seen:
            return status
    return "complete"


def _split_refusal(ce_service, region, internal, rows, entries, amount,
                   ignore_gaps=False, also_regional=False):
    """Why this bucket may not be divided, or "" when it may - the sentence is the
    finding. ignore_gaps: dividing one usage-type line only."""
    if not region and not rows:
        return ("AWS gives no region for this charge, so there is no set of "
                "scanned resources to divide it across")
    where = region or "the account-wide scope"
    if amount <= ZERO:
        return "zero or credit-offset for this period - nothing to divide"
    if not internal:
        return ("nothing in this scan collects this service, so there is no "
                "row this charge could belong to")
    incomplete = [name for name in internal
                  if _region_status(entries, name, region) in _INCOMPLETE]
    if incomplete:
        return (f"the scan was refused or cut short on {', '.join(incomplete)} "
                f"in {where}, so it may not have found everything this charge "
                "covers")
    gaps = BILLING_COVERAGE.get(ce_service, ((), ()))[1]
    if not region and also_regional and not ignore_gaps:
        return ("AWS bills most of this service by region and this scan records "
                "these resources without one, so this line is not their cost")
    if gaps and not ignore_gaps:
        return (f"this bill also covers {', '.join(gaps)}, which this scan "
                "does not collect, so any share would include money that is "
                "not theirs")
    if not rows:
        return (f"no {', '.join(internal)} was found in {where}, so the "
                "charge belongs to something this report does not hold")
    return ""


def _standing_charge(billing, entry, matching):
    """(amount, rows, usage types) of account-wide usage-type lines that are a standing
    charge for rows in `matching`, or None."""
    if entry.region:
        return None
    amount, types, names = ZERO, set(), []
    for line in billing.usage_types_for(entry.service):
        for fragment, resource_types in USAGE_TYPE_RESOURCE_TYPES.items():
            if fragment in line.usage_type and line.amount > ZERO:
                amount += line.amount
                types.update(resource_types)
                names.append(line.usage_type)
    rows = [r for r in matching if r.get("service") in types]
    if amount <= ZERO or not rows:
        return None
    # Never more than the cell holds: the line is account-wide, the cell is not.
    return min(amount, entry.amount), rows, sorted(set(names))


def _divide(rows, pool, billing, label, basis):
    """Give each row an even share of `pool`; return what was allocated. Shared rows
    count in the divisor but are paid nothing."""
    share = (pool / len(rows)).quantize(CENT, rounding=ROUND_DOWN)
    allocated = ZERO
    for row in rows:
        if row.get("membership") == "shared":
            row["cost"] = attribution_for(
                billing, state=SHARED_UNALLOCATED, bucket=label,
                explanation=(
                    f"Used by more than one project, so its share of {label} "
                    "is left unallocated - charging each project would count "
                    "the same money twice.")).as_dict()
        else:
            row["cost"] = attribution_for(
                billing, state=ESTIMATED, amount=share, bucket=label,
                explanation=(
                    f"An even share of {basis}, across the {len(rows)} "
                    "resource(s) this scan found there. An estimate, not a "
                    "measured per-resource charge.")).as_dict()
            allocated += share
    return allocated


def attribute_costs(all_rows, billing, coverage_entries=None):
    """Write each row's CostAttribution (in place) and return the bucket-level report."""
    entries = list(coverage_entries or [])
    rows = list(all_rows or [])

    if not billing.queried:
        note = attribution_for(billing, state=NOT_QUERIED, explanation=(
            "Cost Explorer was not queried (--no-cost). This does not mean "
            "free."))
        for row in rows:
            row["cost"] = note.as_dict()
        return CostReport(billing=billing, buckets=())

    if billing.error and not billing.amounts:
        note = attribution_for(billing, state=UNAVAILABLE, explanation=(
            f"Cost Explorer lookup failed: {billing.error}"))
        for row in rows:
            row["cost"] = note.as_dict()
        return CostReport(billing=billing, buckets=())

    # Every cell of the bill becomes a bucket, matched or not, so no spend disappears.
    buckets, allocated_rows = [], set()
    for entry in billing.amounts:
        internal = _internal_services(entry.service)
        # Rows already claimed are excluded, so no row gets two shares.
        matching = [r for r in rows
                    if r.get("service") in internal
                    and _bill_region(r) == entry.region
                    and id(r) not in allocated_rows]
        label = (f"'{entry.service}'"
                 + (f" in {entry.region}" if entry.region else " (no region)"))
        line = _standing_charge(billing, entry, matching)
        refusal = _split_refusal(
            entry.service, entry.region, internal, matching, entries,
            entry.amount, ignore_gaps=line is not None,
            also_regional=any(a.region and a.amount > ZERO
                              for a in billing.for_service(entry.service)))
        allocated_rows.update(id(row) for row in matching)
        if refusal:
            note = attribution_for(
                billing, state=SHARED_UNALLOCATED, bucket=label,
                explanation=f"Held against {label} and not divided: {refusal}.")
            for row in matching:
                row["cost"] = note.as_dict()
            buckets.append(BucketTotal(
                service=entry.service, region=entry.region, amount=entry.amount,
                row_count=len(matching), allocated=ZERO,
                state=SHARED_UNALLOCATED, explanation=refusal))
            continue

        if line is None:
            allocated = _divide(matching, entry.amount, billing, label,
                                f"what AWS billed as {label}")
            buckets.append(BucketTotal(
                service=entry.service, region=entry.region, amount=entry.amount,
                row_count=len(matching), allocated=allocated, state=ESTIMATED,
                explanation=(f"Divided evenly across {len(matching)} scanned "
                             f"resource(s); the remainder is unallocated.")))
            continue

        # One usage-type line of a bill with gaps: the line is divided and the
        # rest is a second bucket, so the remainder never reads as rounding.
        pool, paid, usage_names = line
        said = ", ".join(usage_names)
        allocated = _divide(paid, pool, billing, label,
                            f"the {said} line of what AWS billed as {label}")
        gaps = ", ".join(BILLING_COVERAGE.get(entry.service, ((), ()))[1])
        rest = (f"the rest of this bill covers {gaps}, which this scan does "
                "not collect")
        for row in matching:
            if not any(row is p for p in paid):
                row["cost"] = attribution_for(
                    billing, state=SHARED_UNALLOCATED, bucket=label,
                    explanation=f"Held against {label} and not divided: {rest}.").as_dict()
        buckets.append(BucketTotal(
            service=entry.service, region=entry.region, amount=pool,
            row_count=len(paid), allocated=allocated, state=ESTIMATED,
            explanation=(f"The {said} line, divided evenly across {len(paid)} "
                         "scanned resource(s).")))
        if entry.amount > pool:
            buckets.append(BucketTotal(
                service=entry.service, region=entry.region,
                amount=entry.amount - pool, row_count=0, allocated=ZERO,
                state=SHARED_UNALLOCATED, explanation=rest))

    for row in rows:
        if id(row) in allocated_rows:
            continue
        row["cost"] = _unmatched(row, billing).as_dict()

    return CostReport(billing=billing, buckets=tuple(buckets))


def _unmatched(row, billing):
    """A row no cell of the bill matched: unmappable, not billed, or billed elsewhere."""
    buckets = ce_services_for(row.get("service", ""))
    if not buckets:
        return attribution_for(billing, state=UNAVAILABLE, explanation=(
            "No Cost Explorer mapping for this resource type, so its cost is "
            "unknown, not zero."))
    if not billing.complete:
        return attribution_for(billing, state=UNAVAILABLE, explanation=(
            "The billing response was incomplete, so no charge here proves "
            "nothing."))
    # An account-wide row and a bill that names regions: the charge exists and
    # cannot be placed, which is not zero.
    if row.get("region") == ACCOUNT_WIDE and any(
            a.amount > ZERO and a.region for name in buckets
            for a in billing.for_service(name)):
        return attribution_for(billing, state=SHARED_UNALLOCATED, bucket=buckets[0],
                               explanation=(
            f"AWS bills '{buckets[0]}' by region and this scan does not record "
            "a region for this resource, so no region's charge can be put "
            "against it."))
    return attribution_for(billing, state=ESTIMATED, amount=ZERO, bucket=buckets[0],
                           explanation=(
        f"Cost Explorer reported no '{buckets[0]}' charges in "
        f"{row.get('region') or 'this scope'}, so this resource's share is "
        "zero."))


def total_spend(billing):
    """The account's spend over the period, or None (never 0) when not queried."""
    return billing.total if billing.queried else None
