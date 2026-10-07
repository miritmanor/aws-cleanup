"""Transit gateways and Site-to-Site VPN connections: each attachment and each
VPN connection bills about $36/month for as long as it exists, used or not."""

from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.calls import paged, tags_to_dict
from ...collect.cloudwatch import activity_note, cw_activity
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago, flag_from_activity
from ...text import join_nonempty

_GONE = ("deleted", "deleting", "failed", "rejected")


def _ec2(session, region):
    return session.client("ec2", region_name=region, config=RETRY_CONFIG)


def _attachments(ec2):
    """{transit gateway id: [live attachment, ...]}."""
    items, _error = paged(ec2, "describe_transit_gateway_attachments", "TransitGatewayAttachments",
                          service="TransitGateway")
    out = {}
    for item in items:
        if item.get("State") not in _GONE:
            out.setdefault(item.get("TransitGatewayId"), []).append(item)
    return out


def collect_transit_gateways(session, region):
    ec2 = _ec2(session, region)
    gateways, page_error = paged(ec2, "describe_transit_gateways", "TransitGateways", service="TransitGateway")
    if page_error and not gateways:
        return [error_row("TransitGateway", region, "ERROR", page_error)]
    attachments = _attachments(ec2) if gateways else {}
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    for gateway in gateways:
        if gateway.get("State") in _GONE:
            continue
        gid, tags = gateway["TransitGatewayId"], tags_to_dict(gateway.get("Tags", []))
        attached = attachments.get(gid, [])
        created = gateway.get("CreationTime")
        evidence = cw_activity(cw, "AWS/TransitGateway", "BytesIn",
                               [{"Name": "TransitGateway", "Value": gid}], stat="Sum")
        last_used = evidence.last_activity
        row = new_row(
            "TransitGateway", region, gid, tags.get("Name") or gid, created, last_used,
            days_ago(last_used) if last_used else days_ago(created), not evidence.used,
            flag_from_activity(evidence, days_ago(created)),
            activity_note(evidence, "BytesIn CloudWatch metric used as activity proxy")
            + f" Each attachment bills about $36/month while it exists ({len(attached)} now), plus per GB. ",
            tags=tags, description=join_nonempty([gateway.get("Description", ""),
                                                  f"{len(attached)} attachment(s)"], ", "),
            arn=gateway.get("TransitGatewayArn", ""), activity=evidence)
        raw_capture.record("TransitGateway", region, gid, gateway)
        for item in attached:
            if item.get("ResourceType") == "vpc":
                add_edge(row, item.get("ResourceId"), "attaches VPC", "TransitGatewayAttachments",
                         conn_type="transitgateway.vpc.attachment", target_service="VPC")
        rows.append(row)
    return rows


def collect_vpn_connections(session, region):
    ec2 = _ec2(session, region)
    connections, page_error = paged(ec2, "describe_vpn_connections", "VpnConnections",
                                    service="VPNConnection")
    if page_error and not connections:
        return [error_row("VPNConnection", region, "ERROR", page_error)]
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    for vpn in connections:
        if vpn.get("State") in _GONE:
            continue
        vid, tags = vpn["VpnConnectionId"], tags_to_dict(vpn.get("Tags", []))
        tunnels_up = sum(1 for t in vpn.get("VgwTelemetry", []) if t.get("Status") == "UP")
        evidence = cw_activity(cw, "AWS/VPN", "TunnelDataIn", [{"Name": "VpnId", "Value": vid}], stat="Sum")
        last_used = evidence.last_activity
        row = new_row(
            "VPNConnection", region, vid, tags.get("Name") or vid, None, last_used,
            days_ago(last_used), not evidence.used, flag_from_activity(evidence, None),
            activity_note(evidence, "TunnelDataIn CloudWatch metric used as activity proxy")
            + " A VPN connection bills about $36/month while it exists, tunnels up or not. ",
            tags=tags, description=join_nonempty([vpn.get("State", ""), f"{tunnels_up} tunnel(s) up",
                                                  vpn.get("CustomerGatewayId", "")], ", "),
            activity=evidence)
        raw_capture.record("VPNConnection", region, vid, vpn)
        add_edge(row, vpn.get("TransitGatewayId"), "connects through", "VpnConnection TransitGatewayId",
                 conn_type="vpnconnection.transitgateway.attachment", target_service="TransitGateway")
        rows.append(row)
    return rows
