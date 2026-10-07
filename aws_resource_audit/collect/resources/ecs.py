"""ECS clusters and services. The service is what runs (and, on Fargate, bills);
its current task definition names the images, role and network it runs with."""

from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.arns import probable_resource_id_from_arn
from ...collect.calls import paged, safe_call, tags_to_dict
from ...collect.uri_refs import ecr_repository_from_image
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago
from ...text import join_nonempty

def _tags(resource):
    return tags_to_dict(resource.get("tags", []), "key", "value")


def _service_row(elbv2, region, cluster_name, svc, task_def):
    name = svc["serviceName"]
    running, desired = svc.get("runningCount", 0), svc.get("desiredCount", 0)
    created = svc.get("createdAt")
    flag = (f"ACTIVE ({running} task(s) running)" if running
            else "STALE (scaled to zero - nothing runs)" if not desired
            else f"UNKNOWN ({desired} desired, none running)")
    row = new_row(
        "ECSService", region, f"{cluster_name}/{name}", name, created, None, days_ago(created), True,
        flag, "Fargate tasks bill per vCPU- and GB-hour while running; EC2-backed tasks bill "
        "through their instances. A service at zero costs nothing. ", tags=_tags(svc),
        description=join_nonempty([svc.get("launchType", ""), f"{running}/{desired} running",
                                   f"cluster {cluster_name}"], ", "),
        arn=svc.get("serviceArn", ""))
    raw_capture.record("ECSService", region, f"{cluster_name}/{name}", svc)
    add_edge(row, cluster_name, "runs in cluster", "service clusterArn",
             conn_type="ecsservice.ecscluster.membership", target_service="ECSCluster")
    network = (svc.get("networkConfiguration") or {}).get("awsvpcConfiguration") or {}
    for subnet_id in network.get("subnets", []):
        add_edge(row, subnet_id, "runs in subnet", "service awsvpcConfiguration.subnets",
                 conn_type="ecsservice.subnet.placement", target_service="Subnet")
    for group_id in network.get("securityGroups", []):
        add_edge(row, group_id, "uses security group", "service awsvpcConfiguration.securityGroups",
                 conn_type="ecsservice.securitygroup.membership", target_service="SecurityGroup")
    for balancer in svc.get("loadBalancers", []):
        _link_target_group(elbv2, row, balancer.get("targetGroupArn"))
    role, _svc = probable_resource_id_from_arn(task_def.get("taskRoleArn") or "")
    add_edge(row, role, "runs as", "task definition taskRoleArn",
             conn_type="ecsservice.iamrole.task-role", target_service="IAMRole")
    for container in task_def.get("containerDefinitions", []):
        repository = ecr_repository_from_image(container.get("image"))
        if repository:
            add_edge(row, repository, "runs image from", "task definition image",
                     conn_type="ecsservice.ecrrepository.image", target_service="ECRRepository")
    return row


def _link_target_group(elbv2, row, tg_arn):
    """The target group, and the load balancer behind it: Fargate registers IP
    targets, which name no resource, so this is the only line from the balancer."""
    if not tg_arn:
        return
    add_edge(row, probable_resource_id_from_arn(tg_arn)[0], "registers with target group",
             "service loadBalancers.targetGroupArn", conn_type="ecsservice.targetgroup.attachment",
             target_service="TargetGroup")
    resp = safe_call(elbv2.describe_target_groups, TargetGroupArns=[tg_arn])
    for group in ([] if "__error__" in resp else resp.get("TargetGroups", [])):
        for lb_arn in group.get("LoadBalancerArns", []):
            parts = lb_arn.split("/")
            add_edge(row, parts[2] if len(parts) >= 4 else None, "receives traffic from",
                     "target group LoadBalancerArns", conn_type="ecsservice.loadbalancer.target",
                     target_service="LoadBalancer")


def _services(ecs, cluster_arn):
    arns, _error = paged(ecs, "list_services", "serviceArns", service="ECSService", cluster=cluster_arn)
    out = []
    for start in range(0, len(arns), 10):   # DescribeServices takes at most ten
        resp = safe_call(ecs.describe_services, cluster=cluster_arn,
                         services=arns[start:start + 10], include=["TAGS"])
        out.extend([] if "__error__" in resp else resp.get("services", []))
    return out


def _container_instances(ecs, cluster_arn):
    """EC2 instance ids registered with a cluster: its capacity when it is not Fargate."""
    arns, _error = paged(ecs, "list_container_instances", "containerInstanceArns",
                         service="ECSCluster", cluster=cluster_arn)
    ids = []
    for start in range(0, len(arns), 100):   # DescribeContainerInstances takes at most 100
        resp = safe_call(ecs.describe_container_instances, cluster=cluster_arn,
                         containerInstances=arns[start:start + 100])
        ids += [] if "__error__" in resp else [c.get("ec2InstanceId") for c in resp.get("containerInstances", [])]
    return sorted(i for i in ids if i)


def collect_ecs(session, region):
    ecs = session.client("ecs", region_name=region, config=RETRY_CONFIG)
    elbv2 = session.client("elbv2", region_name=region, config=RETRY_CONFIG)
    arns, page_error = paged(ecs, "list_clusters", "clusterArns", service="ECSCluster")
    if page_error and not arns:
        return [error_row("ECSCluster", region, "ERROR", page_error)]
    resp = safe_call(ecs.describe_clusters, clusters=arns, include=["TAGS"]) if arns else {}
    rows = []
    for cluster in ([] if "__error__" in resp else resp.get("clusters", [])):
        name = cluster["clusterName"]
        busy = cluster.get("activeServicesCount", 0) or cluster.get("runningTasksCount", 0)
        instances = (_container_instances(ecs, cluster["clusterArn"])
                     if cluster.get("registeredContainerInstancesCount") else [])
        row = new_row(
            "ECSCluster", region, name, name, None, None, None, True,
            "ACTIVE (has services or running tasks)" if busy else "STALE (no services, no tasks)",
            "A cluster is free; its tasks and container instances are what bill. ",
            tags=_tags(cluster), arn=cluster.get("clusterArn", ""),
            description=f"{cluster.get('activeServicesCount', 0)} service(s), "
                        f"{cluster.get('runningTasksCount', 0)} running task(s), "
                        f"{len(instances)} container instance(s)")
        raw_capture.record("ECSCluster", region, name, cluster)
        for instance_id in instances:
            add_edge(row, instance_id, "runs tasks on", "DescribeContainerInstances ec2InstanceId",
                     conn_type="ecscluster.ec2instance.container-instance", target_service="EC2Instance")
        rows.append(row)
        for svc in _services(ecs, cluster["clusterArn"]):
            task = safe_call(ecs.describe_task_definition, taskDefinition=svc.get("taskDefinition", ""))
            task_def = {} if "__error__" in task else task.get("taskDefinition", {})
            rows.append(_service_row(elbv2, region, name, svc, task_def))
    return rows
