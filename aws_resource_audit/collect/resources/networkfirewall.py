"""Network Firewall: the VPC firewall, about $290/month per endpoint (one per AZ)
while it exists, plus per GB. Each endpoint sits in a subnet of the firewall's VPC."""

from ... import activity
from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.calls import paged, safe_call, tags_to_dict
from ...collect.cloudwatch import activity_note, cw_activity
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago, flag_from_activity
from ...text import join_nonempty


def _traffic(cw, name, zones):
    """ReceivedPackets is published per AZ; the busiest endpoint speaks for the firewall."""
    return activity.best_of(*[
        cw_activity(cw, "AWS/NetworkFirewall", "ReceivedPackets",
                    [{"Name": "FirewallName", "Value": name}, {"Name": "Engine", "Value": "Stateless"},
                     {"Name": "AvailabilityZone", "Value": zone}], stat="Sum")
        for zone in zones])


def collect_network_firewalls(session, region):
    client = session.client("network-firewall", region_name=region, config=RETRY_CONFIG)
    firewalls, page_error = paged(client, "list_firewalls", "Firewalls", service="NetworkFirewall")
    if page_error and not firewalls:
        return [error_row("NetworkFirewall", region, "ERROR", page_error)]
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    for summary in firewalls:
        name = summary.get("FirewallName")
        resp = safe_call(client.describe_firewall, FirewallArn=summary.get("FirewallArn"))
        if "__error__" in resp:
            rows.append(error_row("NetworkFirewall", region, name, resp["__error__"]))
            continue
        firewall, status = resp.get("Firewall", {}), resp.get("FirewallStatus", {})
        zones = sorted(status.get("SyncStates") or {})
        subnets = [m.get("SubnetId") for m in firewall.get("SubnetMappings", [])]
        evidence = _traffic(cw, name, zones)
        notes = activity_note(evidence, "ReceivedPackets CloudWatch metric used as activity proxy")
        notes += (" Each firewall endpoint bills about $290/month while it exists, plus per GB "
                  "processed, whether or not traffic is routed through it. ")
        row = new_row(
            "NetworkFirewall", region, name, name, None, evidence.last_activity,
            days_ago(evidence.last_activity),
            not evidence.used, flag_from_activity(evidence, None), notes,
            tags=tags_to_dict(firewall.get("Tags", [])),
            description=join_nonempty([status.get("Status", ""), f"{len(subnets)} endpoint(s)"], ", "),
            vpc_id=firewall.get("VpcId"), arn=firewall.get("FirewallArn", ""), activity=evidence)
        raw_capture.record("NetworkFirewall", region, name, resp)
        for subnet_id in subnets:
            add_edge(row, subnet_id, "has an endpoint in subnet", "Firewall SubnetMappings",
                     conn_type="networkfirewall.subnet.placement", target_service="Subnet")
        rows.append(row)
    return rows
