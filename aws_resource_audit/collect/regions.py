"""Which regions a scan covers, and each region's default VPC."""

from .. import coverage
from ..config import RETRY_CONFIG
from ..console import say
from .calls import safe_call


def get_regions(session, explicit_regions, all_regions, saved=None):
    """Named regions, else every enabled one, else `saved` (region_store), else the session's."""
    if explicit_regions:
        return explicit_regions

    if not all_regions:
        if saved:
            return list(saved)
        if session.region_name:
            return [session.region_name]
        say("No default region configured; defaulting to us-east-1.")
        return ["us-east-1"]

    ec2 = session.client("ec2", region_name=session.region_name or "us-east-1", config=RETRY_CONFIG)
    resp = safe_call(ec2.describe_regions, AllRegions=False)
    if "__error__" in resp:
        say(f"Could not list regions ({resp['__error__']}), defaulting to us-east-1")
        return ["us-east-1"]
    return [r["RegionName"] for r in resp["Regions"]]


def collect_default_vpc_ids(session, region):
    """Default VPC ids for a region, as (ids, known). Sharing a default VPC is not a grouping
    signal; known=False on a failed lookup, so a denial never makes every VPC "chosen"."""
    ec2 = session.client("ec2", region_name=region, config=RETRY_CONFIG)
    resp = safe_call(ec2.describe_vpcs, capability=coverage.INVENTORY,
                     Filters=[{"Name": "isDefault", "Values": ["true"]}])
    if "__error__" in resp:
        return set(), False
    return {v["VpcId"] for v in resp.get("Vpcs", [])}, True
