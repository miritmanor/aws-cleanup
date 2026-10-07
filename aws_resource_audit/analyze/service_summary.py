"""One row per AWS service for the overview's By service view, plus the bill's
own lines for its cost circles (never split across services)."""

from dataclasses import dataclass
from decimal import Decimal

from ..aws_services import service_of
from ..billing import ZERO
from ..config import BILLING_COVERAGE
from ..rows import member_key
from .project_summary import count_usage

KIND_SERVICE = "service"
KIND_BILL = "bill"


@dataclass
class ServiceSummary:
    """One circle's worth of facts: an AWS service, or one line of the bill."""

    id: str
    display_name: str
    kind: str = KIND_SERVICE
    resource_types: tuple = ()
    resource_keys: tuple = ()
    # (resource type, count) pairs, largest first, for the detail box.
    type_counts: tuple = ()
    unused: int = 0
    active: int = 0
    unknown: int = 0
    cost: Decimal = None
    why: str = ""

    @property
    def bubble_id(self):
        return self.id

    @property
    def resource_count(self):
        return len(self.resource_keys)

    @property
    def unused_pct(self):
        if not self.resource_keys:
            return None
        return 100.0 * self.unused / len(self.resource_keys)

    @property
    def all_unknown(self):
        return bool(self.resource_keys) and self.unknown == len(self.resource_keys)

    def as_dict(self):
        return {
            "id": self.id,
            "display_name": self.display_name,
            "kind": self.kind,
            "resource_types": list(self.resource_types),
            "resource_keys": list(self.resource_keys),
            "resource_count": self.resource_count,
            "type_counts": [list(pair) for pair in self.type_counts],
            "unused": self.unused,
            "active": self.active,
            "unknown": self.unknown,
            "unused_pct": self.unused_pct,
            "all_unknown": self.all_unknown,
            "cost": None if self.cost is None else str(self.cost),
            "why": self.why,
        }


@dataclass
class ServiceOverview:
    services: tuple = ()
    bill_lines: tuple = ()
    currency: str = ""
    period_start: str = ""
    period_end: str = ""
    cost_queried: bool = False
    notes: tuple = ()


def _services(rows):
    by_slug = {}
    keys = {}
    types = {}
    for row in rows:
        slug, label = service_of(row.get("service", ""))
        summary = by_slug.get(slug)
        if summary is None:
            summary = by_slug[slug] = ServiceSummary(id=f"svc:{slug}", display_name=label)
            keys[slug], types[slug] = [], {}
        keys[slug].append(member_key(row))
        types[slug][row.get("service", "")] = types[slug].get(row.get("service", ""), 0) + 1
        count_usage(summary, row)
    for slug, summary in by_slug.items():
        summary.resource_keys = tuple(keys[slug])
        summary.type_counts = tuple(sorted(types[slug].items(), key=lambda t: (-t[1], t[0])))
        summary.resource_types = tuple(sorted(types[slug]))
    return tuple(sorted(by_slug.values(),
                        key=lambda s: (-s.resource_count, s.display_name.lower())))


def _bill_lines(billing):
    """Positive bill lines summed over regions, plus the credits left out."""
    totals = {}
    credits = ZERO
    for entry in billing.amounts:
        if entry.amount < ZERO:
            credits += entry.amount
            continue
        totals[entry.service] = totals.get(entry.service, ZERO) + entry.amount
    lines = tuple(
        ServiceSummary(
            id=f"bill:{name}", display_name=name, kind=KIND_BILL,
            resource_types=tuple(BILLING_COVERAGE.get(name, ((), ()))[0]),
            cost=amount,
            why="A line of the AWS bill for the period, as Cost Explorer names it.")
        for name, amount in sorted(totals.items(), key=lambda t: (-t[1], t[0]))
        if amount > ZERO)
    return lines, credits


def summarize_services(rows, billing=None):
    """Summaries by AWS service, and the bill's lines for the cost view."""
    services = _services(rows)
    queried = bool(billing and billing.queried)
    lines, credits = _bill_lines(billing) if queried else ((), ZERO)
    notes = []
    if not queried:
        notes.append("Cost was not queried for this scan, so the cost view is "
                     "unavailable. Scan again with the cost lookup on to fill it in.")
    elif billing.error and not billing.amounts:
        notes.append(f"The Cost Explorer lookup failed ({billing.error}), so the "
                     "cost view is unavailable.")
    if lines:
        notes.append("Cost circles are the lines of the AWS bill, named the way "
                     "AWS names them. They do not match the service circles "
                     "one-to-one: 'EC2 - Other', for example, covers EBS volumes, "
                     "Elastic IPs and NAT gateways.")
    if credits < ZERO:
        notes.append(f"{-credits} {billing.currency} of credits and refunds are "
                     "not drawn - a circle cannot have a negative size.")
    return ServiceOverview(
        services=services,
        bill_lines=lines,
        currency=billing.currency if queried else "",
        period_start=_date_str(billing.period_start) if queried else "",
        period_end=_date_str(billing.period_end) if queried else "",
        cost_queried=queried,
        notes=tuple(notes),
    )


def _date_str(value):
    return value.isoformat() if hasattr(value, "isoformat") else ""
