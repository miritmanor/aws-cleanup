"""EFS file systems: billed per GB stored, with no "last accessed" in the console.
Activity is CloudWatch ClientConnections; mount targets place it in subnets."""

from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.calls import paged, tags_to_dict
from ...collect.cloudwatch import activity_note, cw_activity
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago, flag_from_activity
from ...text import join_nonempty


def _size_gib(fs):
    value = (fs.get("SizeInBytes") or {}).get("Value")
    return f"{value / 1024 ** 3:.1f}GiB" if isinstance(value, (int, float)) else ""


def collect_efs_file_systems(session, region):
    efs = session.client("efs", region_name=region, config=RETRY_CONFIG)
    systems, page_error = paged(efs, "describe_file_systems", "FileSystems",
                                service="EFSFileSystem")
    if page_error and not systems:
        return [error_row("EFSFileSystem", region, "ERROR", page_error)]
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    for fs in systems:
        fid = fs["FileSystemId"]
        created = fs.get("CreationTime")
        tags = tags_to_dict(fs.get("Tags", []))
        evidence = cw_activity(cw, "AWS/EFS", "ClientConnections",
                               [{"Name": "FileSystemId", "Value": fid}], stat="Sum")
        last_used = evidence.last_activity
        # A failed lookup leaves the subnets unknown; the row itself still stands.
        targets, _error = paged(efs, "describe_mount_targets", "MountTargets",
                                service="EFSFileSystem", FileSystemId=fid)
        notes = activity_note(evidence, "ClientConnections CloudWatch metric used as activity proxy")
        notes += " Billed per GB stored whether or not anything mounts it. "
        if not targets:
            notes += "No mount targets: nothing in a VPC can reach it. "
        row = new_row(
            "EFSFileSystem", region, fid, fs.get("Name") or tags.get("Name", ""), created,
            last_used, days_ago(last_used) if last_used else days_ago(created),
            not evidence.used, flag_from_activity(evidence, days_ago(created)), notes, tags=tags,
            description=join_nonempty([_size_gib(fs), fs.get("PerformanceMode", ""),
                                       fs.get("ThroughputMode", "")], ", "),
            arn=fs.get("FileSystemArn", ""), activity=evidence,
            vpc_id=(targets[0].get("VpcId") if targets else None))
        raw_capture.record("EFSFileSystem", region, fid, fs)
        for target in targets:
            add_edge(row, target.get("SubnetId"), "has a mount target in subnet",
                     "MountTargets.SubnetId", conn_type="efs.subnet.placement",
                     target_service="Subnet")
            add_edge(row, target.get("NetworkInterfaceId"), "owns mount-target interface",
                     "MountTargets.NetworkInterfaceId", conn_type="efs.networkinterface.mount-target",
                     target_service="NetworkInterface")
        rows.append(row)
    return rows
