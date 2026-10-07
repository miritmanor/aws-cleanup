"""CloudFront distributions: global, billed by use, and a disabled-but-not-deleted
one is common clutter. Their origins are the edge of the architecture diagram."""

import re

from ... import coverage
from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.calls import paged, safe_call, tags_to_dict
from ...collect.cloudwatch import activity_note, cw_activity
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago, flag_from_activity
from ...text import join_nonempty

_S3_ORIGIN = re.compile(r"^([a-z0-9][a-z0-9.\-]*?)\.s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com$")
_API_ORIGIN = re.compile(r"^([a-z0-9]{10})\.execute-api\.[a-z0-9-]+\.amazonaws\.com$")
_ELB_ORIGIN = re.compile(r"^(?:internal-)?(.+)-[0-9]+\.[a-z0-9-]+\.elb\.amazonaws\.com$")


def _link_origin(row, domain):
    """A "routes to" link for an origin this scan inventories; others stay text."""
    domain = (domain or "").lower()
    for pattern, conn_type, service in (
            (_S3_ORIGIN, "cloudfront.s3bucket.origin", "S3Bucket"),
            (_ELB_ORIGIN, "cloudfront.loadbalancer.origin", "LoadBalancer")):
        match = pattern.match(domain)
        if match:
            if conn_type == "cloudfront.s3bucket.origin":
                add_edge(row, match.group(1), "serves from bucket", "distribution Origins.DomainName",
                         conn_type="cloudfront.s3bucket.origin", target_service="S3Bucket")
            else:
                add_edge(row, match.group(1), "forwards to load balancer",
                         "distribution Origins.DomainName",
                         conn_type="cloudfront.loadbalancer.origin", target_service="LoadBalancer")
            return
    match = _API_ORIGIN.match(domain)
    if match:
        # REST and HTTP API ids share one random namespace, so no type is named.
        add_edge(row, match.group(1), "forwards to API", "distribution Origins.DomainName",
                 conn_type="cloudfront.apigateway.origin", target_service=None)


def collect_cloudfront_distributions(session, region="global"):
    cf = session.client("cloudfront", config=RETRY_CONFIG)
    dists, page_error = paged(cf, "list_distributions", "DistributionList.Items",
                              service="CloudFrontDistribution")
    if page_error and not dists:
        return [error_row("CloudFrontDistribution", "global", "ERROR", page_error)]
    # CloudFront publishes its metrics in us-east-1 only, under Region=Global.
    cw = session.client("cloudwatch", region_name="us-east-1", config=RETRY_CONFIG)
    rows = []
    for dist in dists:
        did = dist["Id"]
        aliases = (dist.get("Aliases") or {}).get("Items", [])
        evidence = cw_activity(cw, "AWS/CloudFront", "Requests", [
            {"Name": "DistributionId", "Value": did}, {"Name": "Region", "Value": "Global"}],
            stat="Sum")
        last_used = evidence.last_activity
        modified = dist.get("LastModifiedTime")
        tags_resp = safe_call(cf.list_tags_for_resource, capability=coverage.TAGS,
                              Resource=dist.get("ARN", "")) if dist.get("ARN") else {}
        tags = {} if "__error__" in tags_resp else tags_to_dict(
            (tags_resp.get("Tags") or {}).get("Items", []))
        enabled = dist.get("Enabled", True)
        flag = (flag_from_activity(evidence, days_ago(modified)) if enabled
                else "STALE (disabled - serves nothing but is not deleted)")
        row = new_row(
            "CloudFrontDistribution", "global", did, aliases[0] if aliases else dist.get("DomainName", ""),
            None, last_used, days_ago(last_used) if last_used else days_ago(modified),
            not evidence.used, flag,
            activity_note(evidence, "Requests CloudWatch metric used as activity proxy")
            + " Billed per request and per GB served; an idle distribution costs little. ",
            tags=tags, description=join_nonempty([dist.get("DomainName", ""), dist.get("Status", ""),
                                                  "" if enabled else "disabled"], ", "),
            arn=dist.get("ARN", ""), activity=evidence)
        raw_capture.record("CloudFrontDistribution", "global", did, dist)
        for origin in (dist.get("Origins") or {}).get("Items", []):
            _link_origin(row, origin.get("DomainName"))
        web_acl = dist.get("WebACLId") or ""
        if web_acl.startswith("arn:"):    # WAFv2: .../global/webacl/<name>/<id>
            add_edge(row, web_acl.split("/")[-2], "protected by web ACL", "distribution WebACLId",
                     conn_type="cloudfront.wafwebacl.protected-by", target_service="WAFWebACL")
        rows.append(row)
    return rows
