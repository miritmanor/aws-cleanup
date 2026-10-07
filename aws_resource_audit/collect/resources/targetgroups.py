"""ELB target groups: free, but one attached to no load balancer routes nothing.
Load balancer -> target links are drawn by edge.py; these rows add the orphans."""

from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.calls import paged
from ...rows import add_edge, error_row, new_row
from ...text import join_nonempty


def _lb_name(arn):
    # arn:...:loadbalancer/app/<name>/<id>
    parts = arn.split("/")
    return parts[2] if len(parts) >= 4 else None


def collect_target_groups(session, region):
    elbv2 = session.client("elbv2", region_name=region, config=RETRY_CONFIG)
    groups, page_error = paged(elbv2, "describe_target_groups", "TargetGroups", service="TargetGroup")
    if page_error and not groups:
        return [error_row("TargetGroup", region, "ERROR", page_error)]
    rows = []
    for group in groups:
        name = group["TargetGroupName"]
        lbs = [_lb_name(arn) for arn in group.get("LoadBalancerArns", [])]
        row = new_row(
            "TargetGroup", region, name, name, None, None, None, True,
            "ACTIVE (attached to a load balancer)" if lbs
            else "STALE (not attached to any load balancer)",
            "Target groups are free; an unattached one is clutter that routes nothing. ",
            description=join_nonempty([group.get("TargetType", ""), group.get("Protocol", ""),
                                       str(group.get("Port", "") or "")], " "),
            vpc_id=group.get("VpcId"), arn=group.get("TargetGroupArn", ""))
        raw_capture.record("TargetGroup", region, name, group)
        for lb in filter(None, lbs):
            add_edge(row, lb, "receives traffic from", "target group LoadBalancerArns",
                     conn_type="targetgroup.loadbalancer.attachment", target_service="LoadBalancer")
        rows.append(row)
    return rows
