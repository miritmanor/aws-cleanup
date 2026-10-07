"""The collection phase: every AWS call a scan makes (regional sweep, global services,
Amplify stack walk, Cost Explorer). Nothing here decides what the results mean."""

import logging
from dataclasses import dataclass, field

from botocore.exceptions import BotoCoreError, ClientError

from .. import coverage
from ..billing import not_queried
from ..console import say
from ..errors import ScanCancelled
from ..scope import scan_account
from ..rows import error_row
from .amplify import apply_amplify_api_links
from .stacks import apply_stack_membership
from .calls import brief_error  # noqa: F401
from .regions import collect_default_vpc_ids, get_regions  # noqa: F401
from .cost import collect_billing
from .resources import (
    COLLECTORS_GLOBAL,
    COLLECTORS_PER_REGION,
    EC2_USAGE_COLLECTORS,
    ROLE_USAGE_COLLECTORS,
    UNUSED_ACCESS_COLLECTORS,
    collect_iam,
    collect_s3_buckets,
    new_ec2_usage_registry,
)

logger = logging.getLogger(__name__)

# Global collectors run once after every region: S3, IAM and COLLECTORS_GLOBAL are
# account-wide, so scanning them per region would report each one N times.
_GLOBAL_COLLECTOR_COUNT = 2 + len(COLLECTORS_GLOBAL)

# Service name on the stand-in row for a collector that raised; deliberately not a resource type.
COLLECTOR_FAILURE_SERVICE = "CollectorFailure"


def _collector_outcomes(found, error_rows):
    """(service, status, error) per resource type one collector produced, so a denial
    on IAM roles does not read as "IAM was denied"."""
    outcomes = []
    for row in error_rows:
        outcomes.append((row["service"], coverage.classify_error(row["notes"]),
                         row["notes"]))
    failed_services = {service for service, _status, _err in outcomes}
    for service in sorted({r["service"] for r in found} - failed_services):
        outcomes.append((service, coverage.COMPLETE, ""))
    return outcomes


def _count_line(found, error_rows):
    """The right-hand side of a scan line: a count, "-" for none, or "ERR"
    plus the one-line reason when the lookup itself failed."""
    if error_rows and not found:
        return f"{'ERR':>5}  {brief_error(error_rows[0]['notes'])}"
    body = f"{found if found else '-':>5}"
    if error_rows:
        return f"{body}  +{len(error_rows)} ERR  {brief_error(error_rows[0]['notes'])}"
    return body


@dataclass
class CollectionResult:
    """What collection produces: found resources only (failed lookups are reported
    and dropped), default VPC ids, and the billing response as it arrived."""

    normalized_scan_results: list = field(default_factory=list)
    default_vpc_ids: set = field(default_factory=set)
    # False when DescribeVpcs failed anywhere, so the set above is not the
    # whole truth. analyze/ reads this before trusting "not a default VPC".
    default_vpcs_known: bool = True
    # Every capability this scan tried, and how it went. The half of the
    # story the rows cannot carry - see coverage.py.
    coverage: list = field(default_factory=list)
    billing: object = field(default_factory=not_queried)
    # How many lookups failed, so the summary never calls a denied account empty.
    failed_lookups: int = 0


def total_collection_steps(regions):
    """How many collector calls a scan will make, counted so a progress bar stays right."""
    return len(COLLECTORS_PER_REGION) * len(regions) + _GLOBAL_COLLECTOR_COUNT


def collect_all(session, regions, *, no_cost=False, report=None, step=0,
                should_stop=None):
    """Scan every region, then global services, then the two late AWS calls. no_cost
    skips the chargeable request; should_stop raises ScanCancelled before the next step."""
    total = total_collection_steps(regions)

    def _report(**event):
        if report is not None:
            report(**event)

    def _stop_if_asked():
        if should_stop is not None and should_stop():
            raise ScanCancelled("The scan was stopped before it finished. "
                                "Nothing was saved.")

    # Shared across regions for the IAM role collector: roles seen attached, and roles
    # Access Analyzer flagged as unused.
    used_role_names = set()
    unused_access_role_names = set()

    def scan(label, fn, *a, region=None, **kw):
        """One aligned progress line per resource type. Failed lookups are said out loud
        and dropped; a ClientError escaping a collector is reported, not fatal."""
        nonlocal step, failed_lookups
        _stop_if_asked()
        say(f"  {label:<32}", end="", flush=True)
        _report(phase="collect", region=region, collector=label,
                step=step, total=total)
        scope_name = region or "global"
        try:
            with coverage.collecting(label, scope_name):
                rows = fn(*a, **kw) or []
        except (ClientError, BotoCoreError) as e:
            # error_row() logs the message; this adds the traceback and the
            # fact that a whole collector escaped rather than one lookup.
            logger.warning("collector %s in %s failed entirely", label,
                           region or "global", exc_info=True)
            rows = [error_row(COLLECTOR_FAILURE_SERVICE, region or "global",
                              label, str(e))]
        step += 1
        errors = [r for r in rows if r.get("flag") == "ERROR"]
        found = [r for r in rows if r.get("flag") != "ERROR"]
        failed_lookups += len(errors)
        # One coverage entry per resource type, so coverage can be asked about a type.
        for service, status, error in _collector_outcomes(found, errors):
            coverage.record(coverage.INVENTORY, status, operation=label,
                            service=service, scope=scope_name,
                            collector=label, error=error,
                            count=sum(1 for r in found if r["service"] == service))
        say(_count_line(len(found), errors), flush=True)
        _report(phase="collect", region=region, collector=label,
                step=step, total=total, count=len(found), errors=len(errors))
        return found

    # Only the tally is kept, to tell "empty account" from "could not look".
    failed_lookups = 0

    normalized_scan_results = []
    default_vpc_ids = set()
    default_vpcs_known = True
    for region in regions:
        say(f"\n{region}")
        # Per-region, since AMI/SG/key-pair references never cross regions.
        ec2_usage = new_ec2_usage_registry()
        with coverage.collecting("default VPCs", region):
            region_defaults, known = collect_default_vpc_ids(session, region)
        default_vpc_ids |= region_defaults
        default_vpcs_known = default_vpcs_known and known
        for label, collector in COLLECTORS_PER_REGION:
            kwargs = {}
            if collector in ROLE_USAGE_COLLECTORS:
                kwargs["role_usage"] = used_role_names
            if collector in UNUSED_ACCESS_COLLECTORS:
                kwargs["unused_access_role_names"] = unused_access_role_names
            if collector in EC2_USAGE_COLLECTORS:
                kwargs["usage"] = ec2_usage
            normalized_scan_results.extend(
                scan(label, collector, session, region, region=region, **kwargs))

    say("\nglobal")
    normalized_scan_results.extend(
        scan("S3 buckets", collect_s3_buckets, session, region="global"))
    normalized_scan_results.extend(
        scan("IAM users/groups/policies/roles", collect_iam, session,
             region="global",
             used_role_names=used_role_names,
             unused_access_role_names=unused_access_role_names))
    for label, collector in COLLECTORS_GLOBAL:
        normalized_scan_results.extend(scan(label, collector, session, region="global"))

    say()
    # Needs the complete row set: it matches an Amplify app's CloudFormation
    # stacks to the resources this scan already found.
    _stop_if_asked()
    _report(phase="collect", stage="stacks", step=step, total=total)
    apply_stack_membership(normalized_scan_results, session)

    _stop_if_asked()
    _report(phase="collect", stage="amplify", step=step, total=total)
    apply_amplify_api_links(normalized_scan_results, session)

    # Last check, and the one that keeps a stopped scan from paying for the bill.
    _stop_if_asked()
    billing = not_queried()
    if not no_cost:
        _report(phase="collect", stage="cost", step=step, total=total)
        with coverage.collecting("cost", "global"):
            # refresh=True is the default and is restated here because it is
            # the property that matters: this scan's figures are this scan's.
            billing = collect_billing(session, account=scan_account(),
                                      refresh=True)
        coverage.record(coverage.CONFIGURATION,
                        coverage.COMPLETE if billing.complete
                        else coverage.classify_error(billing.error),
                        operation="GetCostAndUsage", service="Cost",
                        scope="global", collector="cost", error=billing.error)

    if no_cost:
        # "Not asked for" is a third answer next to "queried" and "failed".
        coverage.record(coverage.CONFIGURATION, coverage.NOT_REQUESTED,
                        operation="GetCostAndUsage", service="Cost",
                        scope="global", collector="cost")

    return CollectionResult(
        normalized_scan_results=normalized_scan_results,
        default_vpc_ids=default_vpc_ids,
        default_vpcs_known=default_vpcs_known,
        coverage=coverage.ledger().entries,
        billing=billing,
        failed_lookups=failed_lookups,
    )
