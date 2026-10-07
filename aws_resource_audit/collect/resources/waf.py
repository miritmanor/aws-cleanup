"""WAF web ACLs: about $5/month each plus rules and requests, and the firewall in
front of a load balancer, API stage or CloudFront distribution."""

from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.calls import paged, safe_call
from ...rows import add_edge, error_row, new_row

# The resource types a regional web ACL can protect that this scan inventories.
_PROTECTED = ("APPLICATION_LOAD_BALANCER", "API_GATEWAY", "COGNITO_USER_POOL")


def _protected(client, arn):
    """[(kind, id)] of what a regional web ACL is associated with."""
    out = []
    for kind in _PROTECTED:
        resp = safe_call(client.list_resources_for_web_acl, WebACLArn=arn, ResourceType=kind)
        for resource_arn in ([] if "__error__" in resp else resp.get("ResourceArns", [])):
            tail = resource_arn.split(":", 5)[-1]
            if kind == "APPLICATION_LOAD_BALANCER":      # loadbalancer/app/<name>/<id>
                out.append((kind, tail.split("/")[2]))
            elif kind == "API_GATEWAY":                  # /restapis/<id>/stages/<stage>
                out.append((kind, tail.split("/")[2]))
            else:                                        # userpool/<id>
                out.append((kind, tail.split("/")[-1]))
    return out


def _acl_row(region, acl, protected, flag):
    name = acl["Name"]
    row = new_row(
        "WAFWebACL", region, name, name, None, None, None, True, flag,
        "A web ACL bills about $5/month plus $1 per rule and a charge per million requests, "
        "whether or not anything is associated with it. ",
        description=(acl.get("Description") or "")[:70], arn=acl.get("ARN", ""))
    raw_capture.record("WAFWebACL", region, name, acl)
    for kind, target in protected:
        if kind == "APPLICATION_LOAD_BALANCER":
            add_edge(row, target, "protects", "ListResourcesForWebACL",
                     conn_type="wafwebacl.loadbalancer.protection", target_service="LoadBalancer")
        elif kind == "API_GATEWAY":
            add_edge(row, target, "protects", "ListResourcesForWebACL",
                     conn_type="wafwebacl.apigateway.protection", target_service="APIGatewayRestApi")
        else:
            add_edge(row, target, "protects", "ListResourcesForWebACL",
                     conn_type="wafwebacl.cognito.protection", target_service="CognitoUserPool")
    return row


def collect_waf_web_acls(session, region):
    """Regional web ACLs, with what each protects."""
    client = session.client("wafv2", region_name=region, config=RETRY_CONFIG)
    acls, page_error = paged(client, "list_web_acls", "WebACLs", service="WAFWebACL", Scope="REGIONAL")
    if page_error and not acls:
        return [error_row("WAFWebACL", region, "ERROR", page_error)]
    rows = []
    for acl in acls:
        protected = _protected(client, acl["ARN"])
        rows.append(_acl_row(region, acl, protected,
                             f"ACTIVE (protects {len(protected)} resource(s))" if protected
                             else "STALE (protects nothing - still billed)"))
    return rows


def collect_waf_cloudfront_acls(session, region="global"):
    """CloudFront-scope web ACLs live in us-east-1; which distribution uses one is
    read from the distribution (cloudfront.wafwebacl.protected-by)."""
    client = session.client("wafv2", region_name="us-east-1", config=RETRY_CONFIG)
    acls, page_error = paged(client, "list_web_acls", "WebACLs", service="WAFWebACL", Scope="CLOUDFRONT")
    if page_error and not acls:
        return [error_row("WAFWebACL", "global", "ERROR", page_error)]
    return [_acl_row("global", acl, [], "UNKNOWN (associations are read from the distributions)")
            for acl in acls]
