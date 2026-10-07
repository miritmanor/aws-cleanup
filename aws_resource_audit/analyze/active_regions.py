"""Which regions hold something: rows found there, bill charges there, or a failed
lookup there ("unknown", kept). The saved region list is the union of the three."""

from dataclasses import dataclass, field

from ..billing import ZERO
from ..config import BILLING_FINDING_MIN_AMOUNT
from ..coverage import INCOMPLETE_STATUSES, INVENTORY

ACCOUNT_WIDE = "global"


@dataclass
class ActiveRegions:
    scanned: list = field(default_factory=list)
    resources: dict = field(default_factory=dict)     # {region: row count}
    billed: dict = field(default_factory=dict)        # {region: Decimal}
    unknown: list = field(default_factory=list)

    @property
    def regions(self):
        return sorted(set(self.resources) | set(self.billed) | set(self.unknown))

    @property
    def billed_not_scanned(self):
        return sorted(set(self.billed) - set(self.scanned))

    @property
    def empty_scanned(self):
        return sorted(set(self.scanned) - set(self.regions))


def find_active_regions(rows, scanned, coverage_entries=None, billing=None):
    resources = {}
    for row in rows:
        region = row.get("region") or ""
        if region and region != ACCOUNT_WIDE:
            resources[region] = resources.get(region, 0) + 1

    billed = {}
    for amount in getattr(billing, "amounts", ()) or ():
        if amount.region and amount.region != ACCOUNT_WIDE:
            billed[amount.region] = billed.get(amount.region, ZERO) + amount.amount
    billed = {r: a for r, a in billed.items()
              if a >= BILLING_FINDING_MIN_AMOUNT and r not in resources}

    unknown = sorted({e.get("scope") for e in coverage_entries or ()
                      if e.get("capability") == INVENTORY
                      and e.get("status") in INCOMPLETE_STATUSES
                      and e.get("scope") in scanned
                      and e.get("scope") not in resources})
    return ActiveRegions(scanned=sorted(scanned), resources=resources,
                         billed=billed, unknown=unknown)
