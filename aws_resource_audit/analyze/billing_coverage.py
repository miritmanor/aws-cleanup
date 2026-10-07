"""The bill checked against the scan, per billed service: unsupported, blocked, not_found
or partial. A type with no ledger entry means "none found", not a failure."""

from dataclasses import dataclass, field

from ..billing import (
    COVERAGE_BLOCKED as BLOCKED,
    COVERAGE_COVERED as COVERED,
    COVERAGE_NOT_A_RESOURCE as NOT_A_RESOURCE,
    COVERAGE_NOT_FOUND as NOT_FOUND,
    COVERAGE_PARTIAL as PARTIAL,
    COVERAGE_PRIORITY as _PRIORITY,
    COVERAGE_UNSUPPORTED as UNSUPPORTED,
    ZERO,
)
from ..coverage import INCOMPLETE_STATUSES, NOT_REQUESTED, WORST_FIRST
from ..config import (
    BILLING_COVERAGE, BILLING_FINDING_MIN_AMOUNT, BILLING_NON_RESOURCE_SERVICES,
    USAGE_TYPE_KINDS,
)

# The verdicts live in billing.py so present/cost.py can word them.

# Ledger statuses that mean a lookup did not complete. "unknown" is
# deliberately not among them - see the module docstring.
_BLOCKED_STATUSES = INCOMPLETE_STATUSES | {NOT_REQUESTED}

# Findings about money going somewhere unaccounted for.
_MONEY_FINDINGS = (UNSUPPORTED, BLOCKED, NOT_FOUND, PARTIAL)


@dataclass(frozen=True)
class ServiceCoverage:
    """One billed service, and how much of it this scan can account for."""

    service: str            # the Cost Explorer name
    amount: object = ZERO
    state: str = COVERED
    # The resource types declared as billing into this service.
    collected: tuple = ()
    # Resource types the same bill covers that nothing here collects.
    gaps: tuple = ()
    # collected type -> the worst ledger status, or "unknown" when the ledger
    # holds no entry, which means the collector found none of them.
    statuses: dict = field(default_factory=dict)
    # collected type -> how many of them this scan holds.
    found: dict = field(default_factory=dict)
    row_count: int = 0
    # (label, amount, collected?) per usage type AWS broke the charge into,
    # largest first. Empty when no breakdown was fetched for this service.
    breakdown: tuple = ()

    @property
    def blocked_types(self):
        """The types whose lookup did not complete, with what happened."""
        return tuple(sorted((name, status) for name, status in self.statuses.items()
                            if status in _BLOCKED_STATUSES))

    @property
    def explained(self):
        """Money AWS attributes to something this scan does collect."""
        return sum((amount for _label, amount, collected in self.breakdown
                    if collected), ZERO)

    @property
    def unexplained(self):
        """Money AWS attributes to something this scan does not collect; zero means
        no sign of, e.g., a NAT gateway."""
        return sum((amount for _label, amount, collected in self.breakdown
                    if not collected), ZERO)


@dataclass(frozen=True)
class BillingCoverageReport:
    """The bill checked against the inventory, as findings."""

    queried: bool = False
    complete: bool = True
    estimated: bool = False
    error: str = ""
    currency: str = ""
    period_start: object = None
    period_end: object = None
    observed_at: object = None
    total: object = ZERO
    services: tuple = ()

    def in_state(self, state):
        """Services in one state costing at least BILLING_FINDING_MIN_AMOUNT."""
        return tuple(s for s in self.services
                     if s.state == state
                     and s.amount >= BILLING_FINDING_MIN_AMOUNT)

    def all_in_state(self, state):
        return tuple(s for s in self.services if s.state == state)

    def listed_services(self):
        """Every service a front end may show, with near-zero findings filtered out."""
        return tuple(s for s in self.services
                     if s.state not in _MONEY_FINDINGS
                     or s.amount >= BILLING_FINDING_MIN_AMOUNT)

    def below_threshold(self):
        """The findings below_threshold_count counts, largest first - named, so a
        reader can see what they are without them crowding the list above."""
        return tuple(sorted((s for s in self.services
                             if ZERO < s.amount < BILLING_FINDING_MIN_AMOUNT
                             and s.state in _MONEY_FINDINGS),
                            key=lambda s: (-s.amount, s.service)))

    def below_threshold_count(self):
        """How many findings were dropped for costing almost (but not exactly) nothing."""
        return sum(1 for s in self.services
                   if ZERO < s.amount < BILLING_FINDING_MIN_AMOUNT
                   and s.state in _MONEY_FINDINGS)

    @property
    def unaccounted(self):
        """What the findings add up to - the headline number."""
        return sum((s.amount for state in _MONEY_FINDINGS
                    for s in self.in_state(state)), ZERO)

    @property
    def has_findings(self):
        return any(self.in_state(state) for state in _MONEY_FINDINGS)


def _worst_status(entries, service):
    """The worst inventory status recorded for one type; "unknown" if none (nothing
    found and nothing refused)."""
    seen = [e.get("status") for e in entries
            if e.get("service") == service and e.get("capability") == "inventory"]
    if not seen:
        return "unknown"
    for status in WORST_FIRST:
        if status in seen:
            return status
    return "complete"


def classify_usage_types(lines):
    """(label, amount, is-collected) per usage type, largest first. Unrecognised types
    count as not collected and keep their raw AWS name."""
    out = []
    for line in lines:
        label, collected = line.usage_type, False
        for fragment, name, is_collected in USAGE_TYPE_KINDS:
            if fragment in line.usage_type:
                label, collected = name, is_collected
                break
        out.append((label, line.amount, collected))
    # Merge the fragments that mapped to the same thing, so "EBS volume
    # storage" is one line rather than one per volume type.
    merged = {}
    for label, amount, collected in out:
        key = (label, collected)
        merged[key] = merged.get(key, ZERO) + amount
    return tuple(sorted(((label, amount, collected)
                         for (label, collected), amount in merged.items()),
                        key=lambda item: -item[1]))


def _state_for(collected, gaps, statuses, row_count, breakdown):
    if not collected:
        return UNSUPPORTED
    if any(status in _BLOCKED_STATUSES for status in statuses.values()):
        return BLOCKED
    if not row_count:
        return NOT_FOUND
    if not gaps:
        return COVERED
    # A declared gap is a finding only while money might be in it.
    if breakdown:
        unexplained = sum((amount for _label, amount, is_collected in breakdown
                           if not is_collected), ZERO)
        if unexplained < BILLING_FINDING_MIN_AMOUNT:
            return COVERED
    return PARTIAL


def assess_billing_coverage(billing, coverage_entries=None, rows=None):
    """Compare a BillingResult with the rows and the ledger (stored dicts). Row counts
    are part of the verdict: billed for something the inventory has none of."""
    entries = list(coverage_entries or [])
    row_counts = {}
    for row in rows or []:
        name = row.get("service", "")
        row_counts[name] = row_counts.get(name, 0) + 1

    services = []
    for ce_service, amount in sorted(billing.by_service().items()):
        if ce_service in BILLING_NON_RESOURCE_SERVICES:
            services.append(ServiceCoverage(service=ce_service, amount=amount,
                                            state=NOT_A_RESOURCE))
            continue
        collected, gaps = BILLING_COVERAGE.get(ce_service, ((), ()))
        statuses = {name: _worst_status(entries, name) for name in collected}
        found = {name: row_counts.get(name, 0) for name in collected}
        total_found = sum(found.values())
        breakdown = classify_usage_types(billing.usage_types_for(ce_service))
        services.append(ServiceCoverage(
            service=ce_service, amount=amount,
            state=_state_for(collected, gaps, statuses, total_found, breakdown),
            collected=tuple(collected), gaps=tuple(gaps), statuses=statuses,
            found=found, row_count=total_found, breakdown=breakdown))

    return BillingCoverageReport(
        queried=billing.queried, complete=billing.complete,
        estimated=billing.estimated, error=billing.error,
        currency=billing.currency, period_start=billing.period_start,
        period_end=billing.period_end, observed_at=billing.observed_at,
        total=billing.total,
        services=tuple(sorted(services,
                              key=lambda s: (_order(s.state), -_sortable(s.amount),
                                             s.service))))


def _order(state):
    return _PRIORITY.index(state) if state in _PRIORITY else len(_PRIORITY)


def _sortable(amount):
    return amount if amount is not None else ZERO
