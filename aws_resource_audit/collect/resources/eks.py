"""EKS clusters: about $73/month for the control plane alone, before any node.
Node groups are summarised on the cluster; their Auto Scaling groups are linked."""

from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.arns import probable_resource_id_from_arn
from ...collect.calls import paged, safe_call
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago
from ...text import join_nonempty


def _node_groups(client, name):
    names, _error = paged(client, "list_nodegroups", "nodegroups", service="EKSCluster", clusterName=name)
    groups = []
    for group_name in names:
        resp = safe_call(client.describe_nodegroup, clusterName=name, nodegroupName=group_name)
        if "__error__" not in resp:
            groups.append(resp.get("nodegroup", {}))
    return groups


def collect_eks_clusters(session, region):
    client = session.client("eks", region_name=region, config=RETRY_CONFIG)
    names, page_error = paged(client, "list_clusters", "clusters", service="EKSCluster")
    if page_error and not names:
        return [error_row("EKSCluster", region, "ERROR", page_error)]
    rows = []
    for name in names:
        detail = safe_call(client.describe_cluster, name=name)
        if "__error__" in detail:
            rows.append(error_row("EKSCluster", region, name, detail["__error__"]))
            continue
        cluster = detail.get("cluster", {})
        groups = _node_groups(client, name)
        profiles, _error = paged(client, "list_fargate_profiles", "fargateProfileNames",
                                 service="EKSCluster", clusterName=name)
        desired = sum((g.get("scalingConfig") or {}).get("desiredSize", 0) for g in groups)
        if desired:
            flag = f"ACTIVE ({desired} node(s) desired)"
        elif profiles:
            flag = "UNKNOWN (Fargate profiles only - no node count to read)"
        else:
            flag = "STALE (no nodes and no Fargate profiles - the control plane bills for nothing)"
        created = cluster.get("createdAt")
        row = new_row(
            "EKSCluster", region, name, name, created, None, days_ago(created), True, flag,
            "The control plane bills about $73/month while the cluster exists; nodes bill as "
            "EC2 instances on top. ", tags=dict(cluster.get("tags") or {}),
            description=join_nonempty([f"Kubernetes {cluster.get('version', '?')}",
                                       f"{len(groups)} node group(s)",
                                       f"{len(profiles)} Fargate profile(s)"], ", "),
            vpc_id=(cluster.get("resourcesVpcConfig") or {}).get("vpcId"), arn=cluster.get("arn", ""))
        raw_capture.record("EKSCluster", region, name, cluster)
        network = cluster.get("resourcesVpcConfig") or {}
        for subnet_id in network.get("subnetIds", []):
            add_edge(row, subnet_id, "runs in subnet", "cluster resourcesVpcConfig.subnetIds",
                     conn_type="ekscluster.subnet.placement", target_service="Subnet")
        for group_id in network.get("securityGroupIds", []) + [network.get("clusterSecurityGroupId")]:
            add_edge(row, group_id, "uses security group", "cluster resourcesVpcConfig",
                     conn_type="ekscluster.securitygroup.membership", target_service="SecurityGroup")
        role, _svc = probable_resource_id_from_arn(cluster.get("roleArn") or "")
        add_edge(row, role, "runs as", "cluster roleArn",
                 conn_type="ekscluster.iamrole.cluster-role", target_service="IAMRole")
        for group in groups:
            for asg in (group.get("resources") or {}).get("autoScalingGroups", []):
                add_edge(row, asg.get("name"), "runs node group on", "nodegroup resources.autoScalingGroups",
                         conn_type="ekscluster.autoscalinggroup.nodegroup",
                         target_service="AutoScalingGroup")
        rows.append(row)
    return rows
