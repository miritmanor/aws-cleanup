"""VPCs, subnets, route tables, internet gateways and NAT gateways: the network
the rest of the account sits in. All free except the NAT gateway."""

import logging

from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.calls import paged, tags_to_dict
from ...collect.cloudwatch import activity_note, cw_activity
from ...staleness import days_ago, flag_from_activity
from ...rows import add_edge, error_row, new_row
from ...text import join_nonempty
from .ec2_usage import usage_is_complete

logger = logging.getLogger(__name__)

_FREE = "VPCs, subnets, route tables and internet gateways are free; this is hygiene, not a saving. "


def _ec2(session, region):
    return session.client("ec2", region_name=region, config=RETRY_CONFIG)


def _name(tags, fallback):
    return tags.get("Name") or fallback


def _in_use_flag(usage, bucket, value, default_note=""):
    """ACTIVE / STALE / UNKNOWN from whether any scanned ENI sits in `value`."""
    if default_note:
        return f"ACTIVE ({default_note})"
    if not usage_is_complete(usage):
        return "UNKNOWN"
    if value in usage[bucket]:
        return "ACTIVE (a scanned network interface is in it)"
    return "STALE (no network interface in it)"


def collect_vpcs(session, region, usage=None):
    """In use when a scanned ENI is in it: every VPC-attached resource has one."""
    vpcs, page_error = paged(_ec2(session, region), "describe_vpcs", "Vpcs", service="VPC")
    if page_error and not vpcs:
        return [error_row("VPC", region, "ERROR", page_error)]
    rows = []
    for vpc in vpcs:
        vid = vpc["VpcId"]
        tags = tags_to_dict(vpc.get("Tags", []))
        default = vpc.get("IsDefault", False)
        notes = _FREE
        if default:
            notes += "The region's default VPC; deleting it is allowed but rarely worth it. "
        row = new_row(
            "VPC", region, vid, _name(tags, vid), "", "", None, True,
            _in_use_flag(usage, "vpc_ids", vid, "default VPC" if default else ""), notes,
            tags=tags, description=vpc.get("CidrBlock", ""), vpc_id=vid,
            aws_default=default)
        raw_capture.record("VPC", region, vid, vpc)
        rows.append(row)
    return rows


def collect_subnets(session, region, usage=None):
    """In use when a scanned ENI is in it. Public or private is decided later,
    from its route table's internet-gateway route; here it is only placed."""
    subnets, page_error = paged(_ec2(session, region), "describe_subnets", "Subnets",
                                service="Subnet")
    if page_error and not subnets:
        return [error_row("Subnet", region, "ERROR", page_error)]
    rows = []
    for subnet in subnets:
        sid = subnet["SubnetId"]
        tags = tags_to_dict(subnet.get("Tags", []))
        zone = subnet.get("AvailabilityZone", "")
        row = new_row(
            "Subnet", region, sid, _name(tags, sid), "", "", None, True,
            _in_use_flag(usage, "subnet_ids", sid), _FREE, tags=tags,
            description=join_nonempty([subnet.get("CidrBlock", ""), zone], " "),
            connections=f"AZ={zone}" if zone else "", vpc_id=subnet.get("VpcId"),
            aws_default=subnet.get("DefaultForAz", False))
        raw_capture.record("Subnet", region, sid, subnet)
        add_edge(row, subnet.get("VpcId"), "is in VPC", "Subnet VpcId",
                 conn_type="subnet.vpc.placement", target_service="VPC")
        rows.append(row)
    return rows


def collect_route_tables(session, region):
    """In use when it is its VPC's main table or is associated with a subnet."""
    tables, page_error = paged(_ec2(session, region), "describe_route_tables", "RouteTables",
                               service="RouteTable")
    if page_error and not tables:
        return [error_row("RouteTable", region, "ERROR", page_error)]
    rows = []
    for table in tables:
        rid = table["RouteTableId"]
        tags = tags_to_dict(table.get("Tags", []))
        assocs = table.get("Associations", [])
        main = any(a.get("Main") for a in assocs)
        subnets = [a["SubnetId"] for a in assocs if a.get("SubnetId")]
        gateways = sorted({r["GatewayId"] for r in table.get("Routes", [])
                           if (r.get("GatewayId") or "").startswith("igw-")})
        if main:
            flag = "ACTIVE (the VPC's main route table)"
        elif subnets:
            flag = "ACTIVE (associated with a subnet)"
        else:
            flag = "STALE (not associated with any subnet)"
        row = new_row(
            "RouteTable", region, rid, _name(tags, rid), "", "", None, True, flag, _FREE,
            tags=tags, description="main" if main else f"{len(subnets)} subnet(s)",
            connections=join_nonempty([f"routes to {g}" for g in gateways], ", "),
            vpc_id=table.get("VpcId"), aws_default=main)
        raw_capture.record("RouteTable", region, rid, table)
        add_edge(row, table.get("VpcId"), "is in VPC", "RouteTable VpcId",
                 conn_type="routetable.vpc.placement", target_service="VPC")
        for subnet_id in subnets:
            add_edge(row, subnet_id, "routes subnet", "RouteTable Associations.SubnetId",
                     conn_type="routetable.subnet.association", target_service="Subnet")
        for gateway in gateways:
            add_edge(row, gateway, "routes to internet gateway", "RouteTable Routes.GatewayId",
                     conn_type="routetable.internetgateway.route", target_service="InternetGateway")
        for nat_id in sorted({r["NatGatewayId"] for r in table.get("Routes", []) if r.get("NatGatewayId")}):
            add_edge(row, nat_id, "routes to NAT gateway", "RouteTable Routes.NatGatewayId",
                     conn_type="routetable.natgateway.route", target_service="NatGateway")
        rows.append(row)
    return rows


def collect_internet_gateways(session, region):
    """In use when attached to a VPC; a detached one does nothing at all."""
    gateways, page_error = paged(_ec2(session, region), "describe_internet_gateways",
                                 "InternetGateways", service="InternetGateway")
    if page_error and not gateways:
        return [error_row("InternetGateway", region, "ERROR", page_error)]
    rows = []
    for gateway in gateways:
        gid = gateway["InternetGatewayId"]
        tags = tags_to_dict(gateway.get("Tags", []))
        vpc_ids = [a["VpcId"] for a in gateway.get("Attachments", [])
                   if a.get("VpcId") and a.get("State") in ("available", "attached")]
        row = new_row(
            "InternetGateway", region, gid, _name(tags, gid), "", "", None, True,
            "ACTIVE (attached to a VPC)" if vpc_ids else "STALE (not attached to any VPC)",
            _FREE, tags=tags, vpc_id=vpc_ids[0] if vpc_ids else None)
        raw_capture.record("InternetGateway", region, gid, gateway)
        for vpc_id in vpc_ids:
            add_edge(row, vpc_id, "attached to VPC", "InternetGateway Attachments.VpcId",
                     conn_type="internetgateway.vpc.attachment", target_service="VPC")
        rows.append(row)
    return rows


# Deleted gateways stay in DescribeNatGateways for about an hour; they bill nothing.
_GONE = ("deleted", "deleting")


def collect_nat_gateways(session, region):
    """Billed hourly whether or not traffic flows, plus per GB. Activity is
    BytesOutToDestination: a gateway that moved nothing is pure cost."""
    gateways, page_error = paged(_ec2(session, region), "describe_nat_gateways", "NatGateways",
                                 service="NatGateway")
    if page_error and not gateways:
        return [error_row("NatGateway", region, "ERROR", page_error)]
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    for nat in gateways:
        if nat.get("State") in _GONE:
            continue
        nid = nat["NatGatewayId"]
        tags = tags_to_dict(nat.get("Tags", []))
        created = nat.get("CreateTime")
        evidence = cw_activity(cw, "AWS/NATGateway", "BytesOutToDestination",
                               [{"Name": "NatGatewayId", "Value": nid}], stat="Sum")
        last_used = evidence.last_activity
        notes = activity_note(evidence, "BytesOutToDestination CloudWatch metric used as activity proxy")
        notes += (" A NAT gateway bills about $32/month per gateway while it exists, "
                  "plus per GB processed, whether or not anything uses it. ")
        if nat.get("ConnectivityType") == "private":
            notes += "Private NAT gateway: no internet egress, no Elastic IP. "
        row = new_row(
            "NatGateway", region, nid, _name(tags, nid), created, last_used,
            days_ago(last_used) if last_used else days_ago(created), not evidence.used,
            flag_from_activity(evidence, days_ago(created)), notes, tags=tags,
            description=join_nonempty([nat.get("ConnectivityType", ""), nat.get("State", "")], " "),
            vpc_id=nat.get("VpcId"), activity=evidence)
        raw_capture.record("NatGateway", region, nid, nat)
        add_edge(row, nat.get("SubnetId"), "sits in subnet", "NatGateway SubnetId",
                 conn_type="natgateway.subnet.placement", target_service="Subnet")
        for address in nat.get("NatGatewayAddresses", []):
            add_edge(row, address.get("AllocationId"), "uses Elastic IP",
                     "NatGatewayAddresses.AllocationId",
                     conn_type="natgateway.elasticip.allocation", target_service="ElasticIP")
        rows.append(row)
    return rows


# Gateway endpoints (S3, DynamoDB) are free and publish no metric; the others bill per AZ-hour.
_BILLED_ENDPOINT_TYPES = ("Interface", "GatewayLoadBalancer")


def collect_vpc_endpoints(session, region):
    """Interface and Gateway Load Balancer endpoints bill per AZ-hour whatever
    their traffic; activity is AWS/PrivateLinkEndpoints BytesProcessed."""
    endpoints, page_error = paged(_ec2(session, region), "describe_vpc_endpoints", "VpcEndpoints",
                                  service="VpcEndpoint")
    if page_error and not endpoints:
        return [error_row("VpcEndpoint", region, "ERROR", page_error)]
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    for ep in endpoints:
        if (ep.get("State") or "").lower() in _GONE:
            continue
        eid = ep["VpcEndpointId"]
        kind = ep.get("VpcEndpointType", "")
        tags = tags_to_dict(ep.get("Tags", []))
        created = ep.get("CreationTimestamp")
        subnets = ep.get("SubnetIds", [])
        if kind in _BILLED_ENDPOINT_TYPES:
            evidence = cw_activity(cw, "AWS/PrivateLinkEndpoints", "BytesProcessed", [
                {"Name": "Endpoint Type", "Value": kind},
                {"Name": "Service Name", "Value": ep.get("ServiceName", "")},
                {"Name": "VPC Endpoint Id", "Value": eid},
                {"Name": "VPC Id", "Value": ep.get("VpcId", "")}], stat="Sum")
            flag = flag_from_activity(evidence, days_ago(created))
            notes = activity_note(evidence, "BytesProcessed CloudWatch metric used as activity proxy")
            notes += (f" Billed about $7.30/month per Availability Zone ({len(subnets) or 1} here) "
                      "plus per GB, whether or not anything uses it. ")
        else:
            evidence = None
            flag = "UNKNOWN (gateway endpoints publish no usage metric)"
            notes = "Gateway endpoints for S3 and DynamoDB are free; removing one is hygiene, not a saving. "
        last_used = evidence.last_activity if evidence else None
        row = new_row(
            "VpcEndpoint", region, eid, _name(tags, ep.get("ServiceName", eid)), created, last_used,
            days_ago(last_used) if last_used else days_ago(created),
            not (evidence and evidence.used), flag, notes, tags=tags,
            description=join_nonempty([kind, ep.get("ServiceName", ""), ep.get("State", "")], " "),
            vpc_id=ep.get("VpcId"), activity=evidence)
        raw_capture.record("VpcEndpoint", region, eid, ep)
        add_edge(row, ep.get("VpcId"), "is in VPC", "VpcEndpoint VpcId",
                 conn_type="vpcendpoint.vpc.placement", target_service="VPC")
        for subnet_id in subnets:
            add_edge(row, subnet_id, "has a network interface in subnet", "VpcEndpoint SubnetIds",
                     conn_type="vpcendpoint.subnet.placement", target_service="Subnet")
        for group in ep.get("Groups", []):
            add_edge(row, group.get("GroupId"), "uses security group", "VpcEndpoint Groups",
                     conn_type="vpcendpoint.securitygroup.membership", target_service="SecurityGroup")
        for table_id in ep.get("RouteTableIds", []):
            add_edge(row, table_id, "is routed by", "VpcEndpoint RouteTableIds",
                     conn_type="vpcendpoint.routetable.association", target_service="RouteTable")
        rows.append(row)
    return rows
