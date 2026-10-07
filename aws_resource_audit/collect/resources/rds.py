"""RDS clusters (Aurora, Serverless v2, Multi-AZ DB clusters) and manual RDS
snapshots - both invisible to describe_db_instances, both billed."""

from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.calls import paged, tags_to_dict
from ...collect.cloudwatch import activity_note, cw_activity
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago, flag_from_activity, flag_stale
from ...text import join_nonempty


# DocumentDB and Neptune share the RDS API but are their own products, bills and
# CloudWatch namespaces: engine -> (cluster type, instance type, namespace, metric).
_FAMILIES = {
    "docdb": ("DocumentDBCluster", "DocumentDBInstance", "AWS/DocDB", "DatabaseConnections"),
    "neptune": ("NeptuneCluster", "NeptuneInstance", "AWS/Neptune", "TotalRequestsPerSec"),
}
_RDS = ("RDSCluster", "RDSInstance", "AWS/RDS", "DatabaseConnections")


def rds_family(engine):
    """(cluster type, instance type, namespace, activity metric) for an engine."""
    return _FAMILIES.get(engine or "", _RDS)


def _rds(session, region):
    return session.client("rds", region_name=region, config=RETRY_CONFIG)


def _subnet_groups(client, operation, key, name_key, service):
    """Subnet group name -> its subnet ids; a failed lookup only loses the placement."""
    groups, _error = paged(client, operation, key, service=service)
    return {g[name_key]: [s["SubnetIdentifier"] for s in g.get("Subnets", []) if s.get("SubnetIdentifier")]
            for g in groups}


def collect_rds_clusters(session, region):
    """A cluster bills for storage and I/O even with no instance running.
    Activity is DatabaseConnections, per cluster."""
    clusters, page_error = paged(_rds(session, region), "describe_db_clusters", "DBClusters",
                                 service="RDSCluster")
    if page_error and not clusters:
        return [error_row("RDSCluster", region, "ERROR", page_error)]
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    subnet_groups = _subnet_groups(_rds(session, region), "describe_db_subnet_groups",
                                   "DBSubnetGroups", "DBSubnetGroupName", "RDSCluster")
    rows = []
    for cluster in clusters:
        cid = cluster["DBClusterIdentifier"]
        cluster_type, instance_type, namespace, metric = rds_family(cluster.get("Engine"))
        created = cluster.get("ClusterCreateTime")
        tags = tags_to_dict(cluster.get("TagList", []))
        evidence = cw_activity(cw, namespace, metric,
                               [{"Name": "DBClusterIdentifier", "Value": cid}], stat="Sum")
        last_used = evidence.last_activity
        members = [m["DBInstanceIdentifier"] for m in cluster.get("DBClusterMembers", [])
                   if m.get("DBInstanceIdentifier")]
        notes = activity_note(evidence, f"{metric} CloudWatch metric used as activity proxy")
        if cluster.get("Status") == "stopped":
            notes += (" Stopped: instance hours pause, storage keeps billing, and AWS "
                      "restarts a stopped cluster after seven days. ")
        if not members:
            notes += " No instances: storage is still billed. "
        serverless = cluster.get("ServerlessV2ScalingConfiguration")
        row = new_row(
            cluster_type, region, cid, tags.get("Name", ""), created, last_used,
            days_ago(last_used) if last_used else days_ago(created), not evidence.used,
            flag_from_activity(evidence, days_ago(created)), notes, tags=tags,
            description=join_nonempty([
                f"{cluster.get('Engine', '')} {cluster.get('EngineVersion', '')}",
                "Serverless v2" if serverless else cluster.get("EngineMode", ""),
                f"{len(members)} instance(s)"], ", "),
            connections=f"subnet-group={cluster.get('DBSubnetGroup', '')}"
            if cluster.get("DBSubnetGroup") else "",
            arn=cluster.get("DBClusterArn", ""), activity=evidence)
        raw_capture.record(cluster_type, region, cid, cluster)
        for member in members:
            add_edge(row, member, "has member instance", "DBClusterMembers",
                     conn_type="rdscluster.rdsinstance.member", target_service=instance_type)
        for subnet_id in subnet_groups.get(cluster.get("DBSubnetGroup"), []):
            add_edge(row, subnet_id, "can run in subnet", "DBSubnetGroup subnets",
                     conn_type="rdscluster.subnet.placement", target_service="Subnet")
        rows.append(row)
    return rows


def _snapshot_row(region, sid, snap, created, source_kind, source_id):
    tags = tags_to_dict(snap.get("TagList", []))
    age = days_ago(created)
    row = new_row(
        "RDSSnapshot", region, sid, tags.get("Name", ""), created, None, age, True,
        flag_stale(None, age, True),
        "Manual snapshots never expire: storage is billed until they are deleted. "
        f"Taken from {source_kind} {source_id or '(unknown)'}; staleness is by age only, "
        "since a snapshot has no usage signal. ", tags=tags,
        description=join_nonempty([snap.get("Engine", ""),
                                   f"{snap.get('AllocatedStorage', '?')}GiB"
                                   if snap.get("AllocatedStorage") else ""], " "),
        arn=snap.get("DBSnapshotArn") or snap.get("DBClusterSnapshotArn", ""))
    raw_capture.record("RDSSnapshot", region, sid, snap)
    # The source is often deleted, which is normal for a backup, so never DANGLING.
    if source_kind == "instance":
        add_edge(row, source_id, "snapshot of instance", "snapshot DBInstanceIdentifier",
                 conn_type="rdssnapshot.rdsinstance.source", target_service="RDSInstance",
                 assert_exists=False)
    else:
        add_edge(row, source_id, "snapshot of cluster", "cluster snapshot DBClusterIdentifier",
                 conn_type="rdssnapshot.rdscluster.source",
                 target_service=rds_family(snap.get("Engine"))[0], assert_exists=False)
    return row


def collect_rds_snapshots(session, region):
    """Manual DB and cluster snapshots. Automated ones expire with their
    retention window and are left out."""
    rds = _rds(session, region)
    rows = []
    for op, key, kind, id_key, source_key in (
            ("describe_db_snapshots", "DBSnapshots", "instance", "DBSnapshotIdentifier",
             "DBInstanceIdentifier"),
            ("describe_db_cluster_snapshots", "DBClusterSnapshots", "cluster",
             "DBClusterSnapshotIdentifier", "DBClusterIdentifier")):
        snaps, page_error = paged(rds, op, key, service="RDSSnapshot", SnapshotType="manual")
        if page_error and not snaps:
            rows.append(error_row("RDSSnapshot", region, "ERROR", page_error))
            continue
        for snap in snaps:
            rows.append(_snapshot_row(region, snap[id_key], snap, snap.get("SnapshotCreateTime"),
                                      kind, snap.get(source_key)))
    return rows
