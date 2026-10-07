"""Redshift provisioned clusters and Serverless workgroups. A paused cluster stops
its node hours but keeps billing for storage, and so does an idle workgroup."""

import re

from ... import coverage
from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.arns import probable_resource_id_from_arn
from ...collect.calls import paged, safe_call, tags_to_dict
from ...collect.cloudwatch import activity_note, cw_activity
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago, flag_from_activity
from ...text import join_nonempty


def collect_redshift_clusters(session, region):
    client = session.client("redshift", region_name=region, config=RETRY_CONFIG)
    clusters, page_error = paged(client, "describe_clusters", "Clusters", service="RedshiftCluster")
    if page_error and not clusters:
        return [error_row("RedshiftCluster", region, "ERROR", page_error)]
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    for cluster in clusters:
        cid = cluster["ClusterIdentifier"]
        created = cluster.get("ClusterCreateTime")
        evidence = cw_activity(cw, "AWS/Redshift", "DatabaseConnections",
                               [{"Name": "ClusterIdentifier", "Value": cid}], stat="Sum")
        last_used = evidence.last_activity
        paused = cluster.get("ClusterStatus") == "paused"
        row = new_row(
            "RedshiftCluster", region, cid, cid, created, last_used,
            days_ago(last_used) if last_used else days_ago(created), not evidence.used,
            "STALE (paused - storage still bills)" if paused
            else flag_from_activity(evidence, days_ago(created)),
            activity_note(evidence, "DatabaseConnections CloudWatch metric used as activity proxy")
            + " Billed per node-hour while running, and for storage even when paused. ",
            tags=tags_to_dict(cluster.get("Tags", [])),
            description=join_nonempty([cluster.get("NodeType", ""),
                                       f"{cluster.get('NumberOfNodes', '?')} node(s)",
                                       cluster.get("ClusterStatus", "")], ", "),
            vpc_id=cluster.get("VpcId"), activity=evidence)
        raw_capture.record("RedshiftCluster", region, cid, cluster)
        for group in cluster.get("VpcSecurityGroups", []):
            add_edge(row, group.get("VpcSecurityGroupId"), "uses security group", "VpcSecurityGroups",
                     conn_type="redshift.securitygroup.membership", target_service="SecurityGroup")
        for role in cluster.get("IamRoles", []):
            add_edge(row, probable_resource_id_from_arn(role.get("IamRoleArn") or "")[0], "can assume",
                     "cluster IamRoles", conn_type="redshift.iamrole.attached-role",
                     target_service="IAMRole")
        rows.append(row)
    return rows


_ROLE_ARN = re.compile(r"arn:aws[\w-]*:iam::\d+:role/[\w+=,.@/-]+")


def _namespaces(client):
    """{namespace name: namespace}. Its iamRoles come back as text wrapping each ARN."""
    items, _error = paged(client, "list_namespaces", "namespaces", service="RedshiftServerlessWorkgroup")
    return {n.get("namespaceName"): n for n in items}


def collect_redshift_serverless_workgroups(session, region):
    """Redshift Serverless: compute bills per RPU-second only while queries run,
    and the namespace's managed storage bills all the time."""
    client = session.client("redshift-serverless", region_name=region, config=RETRY_CONFIG)
    workgroups, page_error = paged(client, "list_workgroups", "workgroups",
                                   service="RedshiftServerlessWorkgroup")
    if page_error and not workgroups:
        return [error_row("RedshiftServerlessWorkgroup", region, "ERROR", page_error)]
    namespaces = _namespaces(client) if workgroups else {}
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    for group in workgroups:
        name = group["workgroupName"]
        created = group.get("creationDate")
        namespace = namespaces.get(group.get("namespaceName")) or {}
        tags = safe_call(client.list_tags_for_resource, capability=coverage.TAGS,
                         resourceArn=group.get("workgroupArn", ""))
        evidence = cw_activity(cw, "AWS/Redshift-Serverless", "ComputeSeconds",
                               [{"Name": "Workgroup", "Value": name}], stat="Sum")
        last_used = evidence.last_activity
        row = new_row(
            "RedshiftServerlessWorkgroup", region, name, name, created, last_used,
            days_ago(last_used) if last_used else days_ago(created), not evidence.used,
            flag_from_activity(evidence, days_ago(created)),
            activity_note(evidence, "ComputeSeconds CloudWatch metric used as activity proxy")
            + " Compute bills per RPU-second only while queries run; the namespace's storage "
              "bills whether or not anything does. ",
            tags={} if "__error__" in tags else tags_to_dict(tags.get("tags", []), "key", "value"),
            description=join_nonempty([f"namespace {group.get('namespaceName', '?')}",
                                       f"{group.get('baseCapacity', '?')} RPU base",
                                       group.get("status", "")], ", "),
            arn=group.get("workgroupArn", ""), activity=evidence)
        raw_capture.record("RedshiftServerlessWorkgroup", region, name, {"workgroup": group, "namespace": namespace})
        for subnet_id in group.get("subnetIds", []):
            add_edge(row, subnet_id, "runs in subnet", "workgroup subnetIds",
                     conn_type="redshiftserverless.subnet.placement", target_service="Subnet")
        for group_id in group.get("securityGroupIds", []):
            add_edge(row, group_id, "uses security group", "workgroup securityGroupIds",
                     conn_type="redshiftserverless.securitygroup.membership", target_service="SecurityGroup")
        for arn in sorted({m for text in namespace.get("iamRoles", []) for m in _ROLE_ARN.findall(text)}):
            add_edge(row, probable_resource_id_from_arn(arn)[0], "can assume", "namespace iamRoles",
                     conn_type="redshiftserverless.iamrole.attached-role", target_service="IAMRole")
        rows.append(row)
    return rows
