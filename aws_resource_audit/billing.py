"""What AWS charged, and how well that is known: period, currency, completeness,
and "not queried" kept distinct from zero. Decimal, never float."""

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal

ZERO = Decimal("0")

# How a row's share of the bill was arrived at. DIRECT is reserved: nothing
# produces a measured per-resource figure yet.
DIRECT = "direct"
ESTIMATED = "estimated"                    # a share, and labelled one
SHARED_UNALLOCATED = "shared_unallocated"  # real money, deliberately unsplit
UNAVAILABLE = "unavailable"                # nobody knows
NOT_QUERIED = "not_queried"                # --no-cost: never asked

# Where an amount came from, for the provenance line under a figure.
SOURCE_COST_EXPLORER = "cost_explorer"
SOURCE_NONE = "none"

# How complete a group's (project's) cost figure is.
COST_FULL = "full"
COST_PARTIAL = "partial"
COST_NONE = "none"
COST_NOT_QUERIED = "not_queried"

# How well this scan covers one billed service, worst first (also the order checked).
COVERAGE_UNSUPPORTED = "unsupported"        # billed, nothing collects it
COVERAGE_BLOCKED = "blocked"                # a lookup was denied or failed
COVERAGE_NOT_FOUND = "not_found"            # collected, and the scan found none
COVERAGE_PARTIAL = "partial"                # the bill also covers uncollected types
COVERAGE_COVERED = "covered"
COVERAGE_NOT_A_RESOURCE = "not_a_resource"  # tax, support, credits

COVERAGE_PRIORITY = (COVERAGE_UNSUPPORTED, COVERAGE_BLOCKED, COVERAGE_NOT_FOUND,
                     COVERAGE_PARTIAL, COVERAGE_COVERED)


def to_decimal(value):
    """A Decimal from whatever AWS or a snapshot handed over."""
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _dump_decimal(value):
    return None if value is None else str(value)


def _dump_date(value):
    return value.isoformat() if hasattr(value, "isoformat") else None


def _load_date(value):
    return date.fromisoformat(value) if value else None


@dataclass(frozen=True)
class BillingAmount:
    """One cell of the Cost Explorer response. Region is "" for charges AWS does
    not attribute to one, so account-wide totals never go to one region's rows."""

    service: str
    region: str
    amount: Decimal

    def as_dict(self):
        return {"service": self.service, "region": self.region,
                "amount": _dump_decimal(self.amount)}


@dataclass(frozen=True)
class UsageTypeAmount:
    """One usage type inside a billed service, e.g. NatGateway-Hours under
    "EC2 - Other". Explains a bucket without claiming a specific resource."""

    service: str
    usage_type: str
    amount: Decimal

    def as_dict(self):
        return {"service": self.service, "usage_type": self.usage_type,
                "amount": _dump_decimal(self.amount)}


@dataclass(frozen=True)
class BillingResult:
    """One billing query with everything needed to read its numbers. `amounts`
    is the only store of figures; by_service() and by_region() derive from it."""

    amounts: tuple = ()
    # Only ever populated for the broad buckets that needed explaining, and
    # only when the bill contained one - it costs a second chargeable request.
    usage_types: tuple = ()
    period_start: date = None
    period_end: date = None
    metric: str = "UnblendedCost"
    currency: str = ""
    account: str = ""
    # AWS's own marker. A period the bill has not closed on is estimated, and
    # a figure AWS is still revising must not be presented as final.
    estimated: bool = False
    # False when a page failed mid-way: pages already read are kept, never totalled.
    complete: bool = True
    # False under --no-cost. Distinct from an empty result, and the whole
    # reason this is a field rather than an absence.
    queried: bool = False
    # When the figures were observed, which is not when the report was
    # rendered and, with the cache below, not necessarily when the scan ran.
    observed_at: datetime = None
    error: str = ""

    @property
    def total(self):
        return sum((a.amount for a in self.amounts), ZERO)

    def by_service(self):
        out = {}
        for entry in self.amounts:
            out[entry.service] = out.get(entry.service, ZERO) + entry.amount
        return out

    def by_region(self):
        out = {}
        for entry in self.amounts:
            out[entry.region] = out.get(entry.region, ZERO) + entry.amount
        return out

    def for_service(self, service):
        return tuple(a for a in self.amounts if a.service == service)

    def usage_types_for(self, service):
        """The usage-type lines under one service, largest first."""
        return tuple(sorted((u for u in self.usage_types if u.service == service),
                            key=lambda u: -u.amount))

    def as_dict(self):
        return {
            "amounts": [a.as_dict() for a in self.amounts],
            "usage_types": [u.as_dict() for u in self.usage_types],
            "period_start": _dump_date(self.period_start),
            "period_end": _dump_date(self.period_end),
            "metric": self.metric,
            "currency": self.currency,
            "account": self.account,
            "estimated": self.estimated,
            "complete": self.complete,
            "queried": self.queried,
            "observed_at": (self.observed_at.isoformat()
                            if hasattr(self.observed_at, "isoformat") else None),
            "error": self.error,
        }


def not_queried():
    """The result of never asking. Not an empty bill - no bill."""
    return BillingResult(queried=False)


def restore_billing(data):
    """A BillingResult back from its serialized form."""
    if not data:
        return not_queried()
    amounts = tuple(
        BillingAmount(service=item.get("service", ""),
                      region=item.get("region", ""),
                      amount=to_decimal(item.get("amount")) or ZERO)
        for item in data.get("amounts") or [])
    usage_types = tuple(
        UsageTypeAmount(service=item.get("service", ""),
                        usage_type=item.get("usage_type", ""),
                        amount=to_decimal(item.get("amount")) or ZERO)
        for item in data.get("usage_types") or [])
    observed = data.get("observed_at")
    return BillingResult(
        amounts=amounts,
        usage_types=usage_types,
        period_start=_load_date(data.get("period_start")),
        period_end=_load_date(data.get("period_end")),
        metric=data.get("metric") or "UnblendedCost",
        currency=data.get("currency") or "",
        account=data.get("account") or "",
        estimated=bool(data.get("estimated")),
        complete=bool(data.get("complete", True)),
        queried=bool(data.get("queried")),
        observed_at=datetime.fromisoformat(observed) if observed else None,
        error=data.get("error") or "",
    )


@dataclass(frozen=True)
class CostAttribution:
    """What this scan can honestly say about one row's cost. `amount` is None
    for every state except ESTIMATED and DIRECT."""

    state: str = NOT_QUERIED
    amount: Decimal = None
    currency: str = ""
    period_start: date = None
    period_end: date = None
    source: str = SOURCE_NONE
    # The Cost Explorer service and region holding this row's unsplit money.
    bucket: str = ""
    explanation: str = ""

    def as_dict(self):
        return {
            "state": self.state,
            "amount": _dump_decimal(self.amount),
            "currency": self.currency,
            "period_start": _dump_date(self.period_start),
            "period_end": _dump_date(self.period_end),
            "source": self.source,
            "bucket": self.bucket,
            "explanation": self.explanation,
        }


def restore_attribution(data):
    if not data:
        return CostAttribution()
    known = set(CostAttribution.__dataclass_fields__)
    fields = {k: v for k, v in data.items() if k in known}
    fields["amount"] = to_decimal(fields.get("amount"))
    fields["period_start"] = _load_date(fields.get("period_start"))
    fields["period_end"] = _load_date(fields.get("period_end"))
    return CostAttribution(**fields)


def attribution_for(billing, *, state, amount=None, bucket="", explanation="",
                    source=SOURCE_COST_EXPLORER):
    """A CostAttribution stamped with the period and currency of `billing`."""
    return CostAttribution(
        state=state, amount=amount, currency=billing.currency,
        period_start=billing.period_start, period_end=billing.period_end,
        source=source if billing.queried else SOURCE_NONE,
        bucket=bucket, explanation=explanation)


@dataclass(frozen=True)
class BucketTotal:
    """One (service, region) cell of the bill. Exists even when no row matched,
    so spend on something the scan did not find is never dropped."""

    service: str            # the Cost Explorer name
    region: str
    amount: Decimal
    row_count: int = 0
    allocated: Decimal = ZERO
    state: str = SHARED_UNALLOCATED
    explanation: str = ""

    @property
    def unallocated(self):
        return self.amount - self.allocated

    def as_dict(self):
        return {
            "service": self.service, "region": self.region,
            "amount": _dump_decimal(self.amount),
            "row_count": self.row_count,
            "allocated": _dump_decimal(self.allocated),
            "unallocated": _dump_decimal(self.unallocated),
            "state": self.state, "explanation": self.explanation,
        }


@dataclass(frozen=True)
class CostReport:
    """The whole cost picture for one scan. Kept apart from the rows so spend
    survives when no row remains to carry it."""

    billing: BillingResult = field(default_factory=not_queried)
    buckets: tuple = ()

    @property
    def total(self):
        return self.billing.total

    @property
    def allocated(self):
        return sum((b.allocated for b in self.buckets), ZERO)

    @property
    def unallocated(self):
        return sum((b.unallocated for b in self.buckets), ZERO)

    def reconciles(self):
        """Allocated plus unallocated equals the total, checked rather than assumed."""
        return self.allocated + self.unallocated == self.total

    def as_dict(self):
        return {"billing": self.billing.as_dict(),
                "buckets": [b.as_dict() for b in self.buckets]}


def with_buckets(report, buckets):
    return replace(report, buckets=tuple(buckets))
