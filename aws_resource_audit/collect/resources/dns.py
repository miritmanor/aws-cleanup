"""Route 53 hosted zones (global, $0.50/month, often left behind) and Resolver endpoints.
Use comes from the free DNSQueries metric, published in us-east-1 for public zones."""

from ... import coverage
from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.calls import paged, parse_aws_ts_string, safe_call, tags_to_dict
from ...collect.cloudwatch import activity_note, cw_activity
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago, flag_from_activity
from ...text import join_nonempty
from .cloudfront import _ELB_ORIGIN

# Every zone starts with an SOA and an NS record; two means nothing was ever added.
_EMPTY_RECORD_COUNT = 2


def _targets(record):
    """DNS names a record points at: an alias target, or a CNAME's values."""
    alias = (record.get("AliasTarget") or {}).get("DNSName")
    if alias:
        return [alias]
    if record.get("Type") == "CNAME":
        return [r.get("Value", "") for r in record.get("ResourceRecords", [])]
    return []


def _link_record(row, record):
    """A "resolves to" line for a record pointing at something this scan lists."""
    name = (record.get("Name") or "").rstrip(".").lower()
    for target in _targets(record):
        target = target.rstrip(".").lower()
        # An API custom domain is named exactly as the record; regional ones use a d-... host.
        regional_domain = target.startswith("d-") and ".execute-api." in target
        if regional_domain or target.endswith(".cloudfront.net"):
            add_edge(row, name, "resolves to API custom domain", f"record {name}",
                     conn_type="route53.apigatewaydomain.alias", assert_exists=regional_domain,
                     target_service="APIGatewayDomainName")
        if target.endswith(".cloudfront.net"):
            # The record name is the distribution's alias, which is how its row is named.
            # An edge-optimized API domain's distribution is AWS's, never in this scan.
            add_edge(row, name, "resolves to distribution", f"record {name}",
                     conn_type="route53.cloudfront.alias", target_match="name",
                     target_service="CloudFrontDistribution",
                     superseded_by="route53.apigatewaydomain.alias")
            continue
        lb = _ELB_ORIGIN.match(target[len("dualstack."):] if target.startswith("dualstack.") else target)
        if lb:
            add_edge(row, lb.group(1), "resolves to load balancer", f"record {name}",
                     conn_type="route53.loadbalancer.alias", target_service="LoadBalancer")
        elif ".s3-website" in target or target.startswith("s3-website"):
            # A website bucket must be named exactly as the record.
            add_edge(row, name, "resolves to website bucket", f"record {name}",
                     conn_type="route53.s3bucket.alias", target_service="S3Bucket")


_COST_NOTE = "About $0.50/month per zone, plus queries. "


def _usage(cw, zid, count, private):
    """(flag, note, evidence) for one zone. Evidence is None when no metric applies."""
    if count <= _EMPTY_RECORD_COUNT:
        return "STALE (no records beyond SOA and NS)", "", None
    if private:
        return ("UNKNOWN (AWS publishes no query count for a private zone)",
                "Private zone, resolvable only inside its VPCs. ", None)
    evidence = cw_activity(cw, "AWS/Route53", "DNSQueries",
                           [{"Name": "HostedZoneId", "Value": zid}])
    note = activity_note(evidence, "Queries are counted only when a resolver reaches "
                                   "Route 53; a zone whose domain is not delegated to it gets none. ")
    return flag_from_activity(evidence, None), note, evidence


def collect_route53_hosted_zones(session, region="global"):
    route53 = session.client("route53", config=RETRY_CONFIG)
    # Route 53 publishes its metrics in us-east-1 and nowhere else.
    cw = session.client("cloudwatch", region_name="us-east-1", config=RETRY_CONFIG)
    zones, page_error = paged(route53, "list_hosted_zones", "HostedZones",
                              service="Route53HostedZone")
    if page_error and not zones:
        return [error_row("Route53HostedZone", "global", "ERROR", page_error)]
    rows = []
    for zone in zones:
        zid = zone["Id"].rsplit("/", 1)[-1]
        count = zone.get("ResourceRecordSetCount", 0)
        private = (zone.get("Config") or {}).get("PrivateZone", False)
        tags_resp = safe_call(route53.list_tags_for_resource, capability=coverage.TAGS,
                              ResourceType="hostedzone", ResourceId=zid)
        tags = {} if "__error__" in tags_resp else tags_to_dict(
            (tags_resp.get("ResourceTagSet") or {}).get("Tags", []))
        flag, note, evidence = _usage(cw, zid, count, private)
        last_used = evidence.last_activity if evidence else None
        row = new_row(
            "Route53HostedZone", "global", zid, zone.get("Name", "").rstrip("."), None,
            last_used, days_ago(last_used), not (evidence and evidence.used), flag,
            _COST_NOTE + note, tags=tags, activity=evidence,
            description=f"{count} record set(s), {'private' if private else 'public'}")
        raw_capture.record("Route53HostedZone", "global", zid, zone)
        records, _error = paged(route53, "list_resource_record_sets", "ResourceRecordSets",
                                service="Route53HostedZone", HostedZoneId=zid)
        for record in records:
            _link_record(row, record)
        rows.append(row)
    return rows


def _endpoint_subnets(client, endpoint_id):
    addresses, _error = paged(client, "list_resolver_endpoint_ip_addresses", "IpAddresses",
                              service="Route53ResolverEndpoint", ResolverEndpointId=endpoint_id)
    return sorted({a["SubnetId"] for a in addresses if a.get("SubnetId")})


def collect_resolver_endpoints(session, region):
    """Resolver endpoints connect a VPC's DNS to another network; each IP address
    is a network interface billed about $90/month, at least two per endpoint."""
    client = session.client("route53resolver", region_name=region, config=RETRY_CONFIG)
    endpoints, page_error = paged(client, "list_resolver_endpoints", "ResolverEndpoints",
                                  service="Route53ResolverEndpoint")
    if page_error and not endpoints:
        return [error_row("Route53ResolverEndpoint", region, "ERROR", page_error)]
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    for endpoint in endpoints:
        eid, direction = endpoint["Id"], endpoint.get("Direction", "")
        metric = "InboundQueryVolume" if direction == "INBOUND" else "OutboundQueryVolume"
        evidence = cw_activity(cw, "AWS/Route53Resolver", metric,
                               [{"Name": "EndpointId", "Value": eid}], stat="Sum")
        last_used, created = evidence.last_activity, parse_aws_ts_string(endpoint.get("CreationTime"))
        tags = safe_call(client.list_tags_for_resource, capability=coverage.TAGS,
                         ResourceArn=endpoint.get("Arn", ""))
        ips = endpoint.get("IpAddressCount", 0)
        row = new_row(
            "Route53ResolverEndpoint", region, eid, endpoint.get("Name") or eid, created, last_used,
            days_ago(last_used) if last_used else days_ago(created), not evidence.used,
            flag_from_activity(evidence, days_ago(created)),
            activity_note(evidence, f"{metric} CloudWatch metric used as activity proxy")
            + f" Billed about $90/month per IP address ({ips} here) plus queries, used or not. ",
            tags={} if "__error__" in tags else tags_to_dict(tags.get("Tags", [])),
            description=join_nonempty([direction.lower(), f"{ips} IP address(es)", endpoint.get("Status", "")], ", "),
            vpc_id=endpoint.get("HostVPCId"), arn=endpoint.get("Arn", ""), activity=evidence)
        raw_capture.record("Route53ResolverEndpoint", region, eid, endpoint)
        for subnet_id in _endpoint_subnets(client, eid):
            add_edge(row, subnet_id, "has an address in subnet", "ListResolverEndpointIpAddresses",
                     conn_type="resolverendpoint.subnet.placement", target_service="Subnet")
        for group_id in endpoint.get("SecurityGroupIds", []):
            add_edge(row, group_id, "uses security group", "endpoint SecurityGroupIds",
                     conn_type="resolverendpoint.securitygroup.membership", target_service="SecurityGroup")
        rows.append(row)
    return rows
