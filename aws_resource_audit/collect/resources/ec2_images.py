"""Launch templates, AMIs and EBS snapshots: no usage signal of their own, so they
are judged by whether anything still references them (ec2_usage.py)."""

from ..calls import parse_aws_ts_string
from ...config import RETRY_CONFIG
from ...staleness import days_ago, flag_stale
from ...collect.calls import paged, safe_call, tags_to_dict
from ...text import join_nonempty
from ...rows import add_edge, error_row, new_row
from ...collect import raw_capture
from .ec2_usage import usage_is_complete


def collect_launch_templates(session, region, usage=None):
    """Launch templates: free, but their AMI, key pair and SG references keep those from
    looking orphaned. A template naming a deleted AMI is a finding."""
    ec2 = session.client("ec2", region_name=region, config=RETRY_CONFIG)
    rows = []
    templates, page_error = paged(ec2, "describe_launch_templates", "LaunchTemplates",
                                  service="LaunchTemplate")
    if page_error and usage is not None:
        usage["complete"] = False
    if page_error and not templates:
        return [error_row("LaunchTemplate", region, "ERROR", page_error)]

    for t in templates:
        name = t["LaunchTemplateName"]
        tags = tags_to_dict(t.get("Tags", []))
        default_ver = t.get("DefaultVersionNumber")
        created = t.get("CreateTime")
        used_by_group = usage_is_complete(usage) and t["LaunchTemplateId"] in usage["launch_template_ids"]
        row = new_row(
            "LaunchTemplate", region, t["LaunchTemplateId"], name,
            created, "", days_ago(created), True,
            "ACTIVE (used by an Auto Scaling group)" if used_by_group
            else flag_stale(None, days_ago(created), True),
            "Launch templates carry no usage data of their own. Auto Scaling groups are scanned "
            "and are their most common consumer; one launched by hand or by another service "
            "leaves no trace here.",
            tags=tags, description=f"{t.get('LatestVersionNumber')} version(s)",
        )
        raw_capture.record("LaunchTemplate", region, t["LaunchTemplateId"], t)
        # Only the default version: what a plain launch uses.
        ver = safe_call(ec2.describe_launch_template_versions,
                        LaunchTemplateId=t["LaunchTemplateId"], Versions=[str(default_ver)])
        if "__error__" in ver:
            row["notes"] += f" Could not read default version ({ver['__error__']})."
        else:
            for v in ver.get("LaunchTemplateVersions", []):
                d = v.get("LaunchTemplateData", {})
                add_edge(row, d.get("ImageId"), "launches AMI", f"launch template v{default_ver} ImageId",
                         conn_type="launchtemplate.ami.image-id", target_service="AMI")
                add_edge(row, d.get("KeyName"), "uses key pair", f"launch template v{default_ver} KeyName",
                         conn_type="launchtemplate.keypair.key-name", target_service="KeyPair")
                sgs = list(d.get("SecurityGroupIds", []))
                for nic in d.get("NetworkInterfaces", []):
                    sgs.extend(nic.get("Groups", []))
                for sg in sgs:
                    add_edge(row, sg, "uses security group", f"launch template v{default_ver}",
                             conn_type="launchtemplate.securitygroup.membership", target_service="SecurityGroup")
                if usage is not None:
                    if d.get("ImageId"):
                        usage["image_ids"].add(d["ImageId"])
                    if d.get("KeyName"):
                        usage["key_names"].add(f"{region}:{d['KeyName']}")
                    usage["sg_ids"].update(sgs)
        rows.append(row)
    return rows


def collect_amis(session, region, usage=None):
    """Self-owned AMIs. Free, but they keep billed snapshots alive. LastLaunchedTime
    is kept about a year, so blank means "not recently", not "never"."""
    ec2 = session.client("ec2", region_name=region, config=RETRY_CONFIG)
    rows = []
    images, page_error = paged(ec2, "describe_images", "Images", service="AMI",
                               Owners=["self"])
    if page_error and usage is not None:
        usage["complete"] = False
    if page_error and not images:
        return [error_row("AMI", region, "ERROR", page_error)]

    for img in images:
        image_id = img["ImageId"]
        created = parse_aws_ts_string(img.get("CreationDate"))
        last_launched = parse_aws_ts_string(img.get("LastLaunchedTime"))
        tags = tags_to_dict(img.get("Tags", []))
        in_use = usage_is_complete(usage) and image_id in usage["image_ids"]
        snapshot_ids = [bdm["Ebs"]["SnapshotId"] for bdm in img.get("BlockDeviceMappings", [])
                        if bdm.get("Ebs", {}).get("SnapshotId")]

        ref_days = days_ago(last_launched) if last_launched else days_ago(created)
        if in_use:
            flag = "ACTIVE (referenced by a scanned instance or launch template)"
        elif last_launched:
            flag = flag_stale(days_ago(last_launched), days_ago(created), False)
        else:
            base = flag_stale(None, days_ago(created), True)
            flag = ("STALE (NO LAUNCH IN ~1 YEAR)" if base.startswith("STALE")
                    else f"{base} - no launch recorded in ~1 year")

        notes = ("AMI itself is free - its backing EBS snapshot(s) are what you pay for, so deregistering "
                 "the AMI without deleting those snapshots saves nothing. ")
        notes += ("LastLaunchedTime is real AWS usage data (updated at most daily, ~1 year retention). "
                  if last_launched else
                  "AWS reports no launch in roughly the last year (LastLaunchedTime is empty). ")
        if img.get("Public"):
            notes += "WARNING: this AMI is shared PUBLICLY. "

        row = new_row(
            "AMI", region, image_id, img.get("Name", ""),
            created, last_launched, ref_days, last_launched is None,
            flag, notes, tags=tags,
            description=join_nonempty([
                img.get("Description", ""),
                f"{img.get('Architecture', '')} {img.get('PlatformDetails', '')}, "
                f"{len(snapshot_ids)} snapshot(s)",
            ]),
        )
        raw_capture.record("AMI", region, image_id, img)
        for sid in snapshot_ids:
            add_edge(row, sid, "backed by snapshot", "AMI BlockDeviceMappings",
                     conn_type="ami.ebssnapshot.block-device", target_service="EBSSnapshot")
            if usage is not None:
                usage["snapshot_ids"].add(sid)
                if not flag.startswith("STALE"):
                    usage["active_ami_snapshot_ids"].add(sid)
        rows.append(row)
    return rows


def collect_ebs_snapshots(session, region, usage=None):
    """Self-owned EBS snapshots: billed per GB. The useful signal is whether one still
    backs an AMI, and its age."""
    ec2 = session.client("ec2", region_name=region, config=RETRY_CONFIG)
    rows = []
    snaps, page_error = paged(ec2, "describe_snapshots", "Snapshots", service="EBSSnapshot", OwnerIds=["self"])
    if page_error and not snaps:
        return [error_row("EBSSnapshot", region, "ERROR", page_error)]

    for s in snaps:
        sid = s["SnapshotId"]
        started = s.get("StartTime")
        backs_ami = usage_is_complete(usage) and sid in usage["snapshot_ids"]
        backs_active_ami = usage_is_complete(usage) and sid in usage["active_ami_snapshot_ids"]
        size = s.get("VolumeSize", "?")

        if backs_active_ami:
            flag = "ACTIVE (backs a registered AMI)"
        elif backs_ami:
            base = flag_stale(None, days_ago(started), True)
            flag = ("STALE (backs an unused AMI)" if base.startswith("STALE")
                    else f"{base} - backs an AMI with no recent launch")
        else:
            flag = flag_stale(None, days_ago(started), True)

        notes = f"Billed for {size}GiB of snapshot storage"
        notes += (" (incremental - deleting one snapshot only frees the blocks no other snapshot "
                  "still needs, so actual savings are usually less than the full size). ")
        if backs_active_ami:
            notes += "Backs a registered AMI - deregister that AMI before deleting this. "
        elif backs_ami:
            notes += ("Backs a registered AMI that itself appears unused (no recent launch) - "
                      "deregistering that AMI would let you delete this snapshot too. ")
        notes += "Snapshots have no last-accessed data; age is the only available signal. "

        row = new_row(
            "EBSSnapshot", region, sid, tags_to_dict(s.get("Tags", [])).get("Name", ""),
            started, "", days_ago(started), True,
            flag, notes, tags=tags_to_dict(s.get("Tags", [])),
            description=f"{size}GiB, tier {s.get('StorageTier', 'standard')}: {s.get('Description', '')[:60]}",
            cost_hint=None if backs_ami else "unattached",
        )
        raw_capture.record("EBSSnapshot", region, sid, s)
        # A deleted source volume is expected for an old backup, so this must
        # not raise DANGLING - assert_exists stays False.
        add_edge(row, s.get("VolumeId"), "snapshot of volume", "snapshot VolumeId",
                 conn_type="ebssnapshot.ebsvolume.volume-id",
                 assert_exists=False, target_service="EBSVolume")
        rows.append(row)
    return rows
