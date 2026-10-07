"""Auto Scaling groups: free themselves, but they own their instances and are
the most common consumer of launch templates, which is what makes those readable."""

from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.arns import probable_resource_id_from_arn
from ...collect.calls import paged, tags_to_dict
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago
from ...text import join_nonempty


def _template_id(group):
    spec = group.get("LaunchTemplate") or (
        (group.get("MixedInstancesPolicy") or {}).get("LaunchTemplate") or {}
    ).get("LaunchTemplateSpecification") or {}
    return spec.get("LaunchTemplateId")


def collect_auto_scaling_groups(session, region, usage=None):
    client = session.client("autoscaling", region_name=region, config=RETRY_CONFIG)
    groups, page_error = paged(client, "describe_auto_scaling_groups", "AutoScalingGroups",
                               service="AutoScalingGroup")
    if page_error and usage is not None:
        usage["complete"] = False
    if page_error and not groups:
        return [error_row("AutoScalingGroup", region, "ERROR", page_error)]
    rows = []
    for group in groups:
        name = group["AutoScalingGroupName"]
        desired = group.get("DesiredCapacity", 0)
        instances = [i["InstanceId"] for i in group.get("Instances", []) if i.get("InstanceId")]
        template_id = _template_id(group)
        created = group.get("CreatedTime")
        flag = (f"ACTIVE ({desired} instance(s) desired)" if desired
                else "STALE (scaled to zero - free, but nothing runs)")
        row = new_row(
            "AutoScalingGroup", region, name, name, created, None, days_ago(created), True, flag,
            "The group is free; its instances and their volumes are what bill. ",
            tags=tags_to_dict(group.get("Tags", [])),
            description=join_nonempty([
                f"min {group.get('MinSize', '?')} / desired {desired} / max {group.get('MaxSize', '?')}",
                "launch configuration (legacy)" if group.get("LaunchConfigurationName") else ""], ", "),
            arn=group.get("AutoScalingGroupARN", ""))
        raw_capture.record("AutoScalingGroup", region, name, group)
        add_edge(row, template_id, "launches from template", "group LaunchTemplate",
                 conn_type="asg.launchtemplate.reference", target_service="LaunchTemplate")
        for instance_id in instances:
            add_edge(row, instance_id, "runs instance", "group Instances",
                     conn_type="asg.ec2.instance-membership", target_service="EC2Instance")
        for tg_arn in group.get("TargetGroupARNs", []):
            add_edge(row, probable_resource_id_from_arn(tg_arn)[0], "registers with target group",
                     "group TargetGroupARNs", conn_type="asg.targetgroup.attachment",
                     target_service="TargetGroup")
        for subnet_id in filter(None, (group.get("VPCZoneIdentifier") or "").split(",")):
            add_edge(row, subnet_id.strip(), "launches into subnet", "group VPCZoneIdentifier",
                     conn_type="asg.subnet.placement", target_service="Subnet")
        if usage is not None and template_id:
            usage["launch_template_ids"].add(template_id)
        rows.append(row)
    return rows
