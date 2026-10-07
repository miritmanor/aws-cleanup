"""EC2 instances, EBS volumes, Elastic IPs, spot requests and reserved instances.
Order matters: instances feed the usage registry (ec2_usage.py) that later collectors read."""

import logging
from botocore.exceptions import BotoCoreError, ClientError
from ..cloudwatch import activity_note, cw_activity
from ..iam_policy import role_name_from_arn
from ...config import RETRY_CONFIG
from ...staleness import days_ago, flag_from_activity
from ...collect.calls import paged, safe_call, tags_to_dict
from ...text import join_nonempty
from ...rows import add_edge, error_row, new_row
from ...collect import raw_capture
from .ec2_usage import usage_is_complete

logger = logging.getLogger(__name__)


def collect_ec2_instances(session, region, role_usage=None, usage=None):
    ec2 = session.client("ec2", region_name=region, config=RETRY_CONFIG)
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    iam = session.client("iam", config=RETRY_CONFIG) if role_usage is not None else None
    rows = []
    paginator = ec2.get_paginator("describe_instances")
    try:
        for page in paginator.paginate():
            for res in page["Reservations"]:
                for inst in res["Instances"]:
                    iid = inst["InstanceId"]
                    created = inst.get("LaunchTime")
                    tags = tags_to_dict(inst.get("Tags", []))
                    name = tags.get("Name", "")
                    role_name = None
                    profile_arn = inst.get("IamInstanceProfile", {}).get("Arn")
                    if profile_arn:
                        role_name = role_name_from_arn(profile_arn)  # instance-profile name; usually matches role name
                        if iam is not None:
                            profile_detail = safe_call(
                                iam.get_instance_profile,
                                InstanceProfileName=role_name,
                            )
                            if "__error__" not in profile_detail:
                                roles = profile_detail.get("InstanceProfile", {}).get("Roles", [])
                                if roles:
                                    role_name = roles[0]["RoleName"]
                                    role_usage.add(role_name)
                    evidence = cw_activity(
                        cw, "AWS/EC2", "CPUUtilization",
                        [{"Name": "InstanceId", "Value": iid}], stat="Sum"
                    )
                    last_used = evidence.last_activity
                    inferred = not evidence.used
                    ref_days = days_ago(last_used) if last_used else days_ago(created)
                    vpc_id = inst.get("VpcId", "")
                    subnet_id = inst.get("SubnetId", "")
                    sg_names = [g.get("GroupName", g.get("GroupId")) for g in inst.get("SecurityGroups", [])]
                    connections = join_nonempty([
                        f"VPC={vpc_id}" if vpc_id else "",
                        f"subnet={subnet_id}" if subnet_id else "",
                        f"SGs=[{','.join(sg_names)}]" if sg_names else "",
                        f"IAM role={role_name}" if role_name else "",
                    ])
                    description = join_nonempty([
                        inst.get("InstanceType", ""),
                        f"AMI {inst.get('ImageId', '')}",
                        inst.get("PlatformDetails", ""),
                    ])
                    row = new_row(
                        "EC2Instance", region, iid, name,
                        created, last_used, ref_days, inferred,
                        flag_from_activity(evidence, days_ago(created)),
                        activity_note(evidence, "CPUUtilization CloudWatch metric (15mo max) used as activity proxy"),
                        tags=tags, description=description, connections=connections,
                        vpc_id=vpc_id, iam_role=role_name,
                    )
                    add_edge(row, role_name, "runs as", "instance profile role",
                             conn_type="ec2.iamrole.instance-profile",
                             target_service="IAMRole")
                    raw_capture.record("EC2Instance", region, iid, inst)
                    # The edge for the graph, and the registry entry for staleness.
                    sg_ids = [g["GroupId"] for g in inst.get("SecurityGroups", []) if g.get("GroupId")]
                    add_edge(row, inst.get("ImageId"), "launched from AMI", "instance ImageId",
                             conn_type="ec2.ami.image-id",
                             assert_exists=False, target_service="AMI")  # AMI may be public/third-party, not in this scan
                    add_edge(row, inst.get("KeyName"), "uses key pair", "instance KeyName",
                             conn_type="ec2.keypair.key-name",
                             target_service="KeyPair")  # KeyName is an arbitrary user string - could coincidentally match anything else with that name
                    for sg in sg_ids:
                        add_edge(row, sg, "uses security group", "instance SecurityGroups",
                                 conn_type="ec2.securitygroup.membership", target_service="SecurityGroup")
                    add_edge(row, inst.get("SubnetId"), "runs in subnet", "instance SubnetId",
                             conn_type="ec2.subnet.placement", target_service="Subnet")
                    if usage is not None:
                        if inst.get("ImageId"):
                            usage["image_ids"].add(inst["ImageId"])
                        if inst.get("KeyName"):
                            usage["key_names"].add(f"{region}:{inst['KeyName']}")
                        usage["sg_ids"].update(sg_ids)
                        # Only RUNNING instances can consume a reservation.
                        if inst["State"]["Name"] == "running" and inst.get("InstanceType"):
                            usage["instance_types"].add(f"{region}:{inst['InstanceType']}")
                    rows.append(row)
    except (ClientError, BotoCoreError) as e:
        # The biggest producer into `usage` failed, so no consumer may conclude "unused".
        if usage is not None:
            usage["complete"] = False
        rows.append(error_row("EC2Instance", region, "ERROR", str(e)))
    return rows


def collect_ebs_volumes(session, region):
    """EBS volumes, resolving what each is attached to and whether that instance is running:
    "in-use" on a stopped instance is flagged likely idle."""
    ec2 = session.client("ec2", region_name=region, config=RETRY_CONFIG)
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    volumes, page_error = paged(ec2, "describe_volumes", "Volumes",
                                service="EBSVolume")
    if page_error and not volumes:
        return [error_row("EBSVolume", region, "ERROR", page_error)]

    # Resolve the power state + Name tag of every instance any volume here is
    # attached to, in one batched call, instead of one call per volume.
    attached_instance_ids = sorted({
        a["InstanceId"] for v in volumes for a in v.get("Attachments", []) if a.get("InstanceId")
    })
    instance_info = {}
    if attached_instance_ids:
        try:
            for page in ec2.get_paginator("describe_instances").paginate(InstanceIds=attached_instance_ids):
                for res in page["Reservations"]:
                    for inst in res["Instances"]:
                        inst_tags = tags_to_dict(inst.get("Tags", []))
                        instance_info[inst["InstanceId"]] = {
                            "name": inst_tags.get("Name", ""),
                            "state": inst["State"]["Name"],
                        }
        except (ClientError, BotoCoreError) as e:
            # Non-fatal: fall back to reporting just the instance ID with no state.
            logger.debug("could not describe instances %s attached to volumes: "
                         "%s", attached_instance_ids, e, exc_info=True)

    for vol in volumes:
        vid = vol["VolumeId"]
        created = vol.get("CreateTime")
        attachments = vol.get("Attachments", [])
        attached = bool(attachments)
        tags = tags_to_dict(vol.get("Tags", []))
        evidence = cw_activity(
            cw, "AWS/EBS", "VolumeReadOps",
            [{"Name": "VolumeId", "Value": vid}], stat="Sum"
        )
        last_used = evidence.last_activity
        inferred = not evidence.used
        ref_days = days_ago(last_used) if last_used else days_ago(created)

        instance_running = None  # None = n/a (unattached), True/False once resolved
        attached_instance_id = ""
        attach_detail = ""
        if attached:
            a = attachments[0]
            attached_instance_id = a.get("InstanceId", "")
            info = instance_info.get(attached_instance_id, {})
            inst_name = info.get("name", "")
            inst_state = info.get("state", "unknown")
            instance_running = inst_state == "running"
            attach_detail = (
                f"EC2 instance {attached_instance_id}" + (f" ({inst_name})" if inst_name else "") +
                f", instance state={inst_state}, device={a.get('Device', '')}, "
                f"attachment state={a.get('State', '')}"
            )

        description = f"{vol.get('VolumeType', '')}, {vol.get('Size', '?')}GiB"

        if not attached:
            flag = "STALE (UNATTACHED)"
            notes = "UNATTACHED - not attached to anything, likely orphaned"
            cost_hint = "unattached"
        elif instance_running is False:
            flag = "STALE (ATTACHED TO STOPPED INSTANCE)"
            notes = (f"Attached to {attach_detail} - AWS reports this volume's state as 'in-use' "
                     "because it's attached, but the instance isn't running, so nothing is actually "
                     "reading/writing it right now. Still billed for storage. " +
                     activity_note(evidence, "VolumeReadOps used as activity proxy"))
            # Attached but idle - not "unattached", which would contradict the connections.
            cost_hint = "attached_idle"
        else:
            flag = flag_from_activity(evidence, days_ago(created))
            notes = f"Attached to {attach_detail}. " + activity_note(evidence, "VolumeReadOps used as activity proxy")
            cost_hint = None

        row = new_row(
            "EBSVolume", region, vid, tags.get("Name", ""),
            created, last_used, ref_days, inferred,
            flag, notes, tags=tags, description=description,
            cost_hint=cost_hint,
        )
        raw_capture.record("EBSVolume", region, vid, vol)
        add_edge(row, attached_instance_id, "attached to",
                 f"EBS attachment ({attach_detail})" if attach_detail else "EBS attachment",
                 conn_type="ebsvolume.ec2.attachment", target_service="EC2Instance")
        rows.append(row)
    return rows


def collect_elastic_ips(session, region):
    ec2 = session.client("ec2", region_name=region, config=RETRY_CONFIG)
    resp = safe_call(ec2.describe_addresses)
    if "__error__" in resp:
        return [error_row("ElasticIP", region, "ERROR", resp["__error__"])]
    rows = []
    for addr in resp.get("Addresses", []):
        associated = "AssociationId" in addr
        tags = tags_to_dict(addr.get("Tags", []))
        connections = f"associated with instance {addr.get('InstanceId')}" if addr.get("InstanceId") else ""
        rows.append(new_row(
            "ElasticIP", region, addr.get("AllocationId", addr.get("PublicIp")),
            addr.get("PublicIp", ""),
            "", "", None, True,
            "STALE (UNASSOCIATED - billed hourly)" if not associated else "ACTIVE",
            "EIPs have no creation timestamp via API; unassociated EIPs incur cost and are a common cleanup target",
            tags=tags, description=addr.get("Domain", ""), connections=connections,
            cost_hint="unattached" if not associated else None,
        ))
        raw_capture.record("ElasticIP", region,
                           addr.get("AllocationId", addr.get("PublicIp")), addr)
    return rows


def collect_spot_instance_requests(session, region):
    """Spot requests: the instance costs money, not the request. Closed requests
    are reported as history, not flagged stale."""
    ec2 = session.client("ec2", region_name=region, config=RETRY_CONFIG)
    rows = []
    reqs, page_error = paged(ec2, "describe_spot_instance_requests", "SpotInstanceRequests", service="SpotInstanceRequest")
    if page_error and not reqs:
        return [error_row("SpotInstanceRequest", region, "ERROR", page_error)]

    for r in reqs:
        state = r.get("State", "")
        created = r.get("CreateTime")
        live = state in ("open", "active")
        row = new_row(
            "SpotInstanceRequest", region, r["SpotInstanceRequestId"],
            r.get("LaunchGroup", ""),
            created, created if live else None, days_ago(created), True,
            "ACTIVE" if live else "HISTORY (closed/cancelled - AWS removes these itself)",
            f"{r.get('Type', '')} request, status: {r.get('Status', {}).get('Message', '')}. "
            "The request itself is free; any instance it launched is billed separately.",
            tags=tags_to_dict(r.get("Tags", [])),
            description=f"spot price {r.get('SpotPrice', '?')}",
        )
        raw_capture.record("SpotInstanceRequest", region,
                           r["SpotInstanceRequestId"], r)
        add_edge(row, r.get("InstanceId"), "launched instance", "spot request InstanceId",
                 conn_type="spotrequest.ec2.instance-id",
                 assert_exists=live, target_service="EC2Instance")
        rows.append(row)
    return rows


def collect_reserved_instances(session, region, usage=None):
    """Reserved instances. An active RI with no running instance of its type is waste;
    a HINT only, since real matching also depends on platform, tenancy and scope."""
    ec2 = session.client("ec2", region_name=region, config=RETRY_CONFIG)
    resp = safe_call(ec2.describe_reserved_instances)   # no paginator on this API
    if "__error__" in resp:
        return [error_row("ReservedInstance", region, "ERROR", resp["__error__"])]

    rows = []
    for ri in resp.get("ReservedInstances", []):
        state = ri.get("State", "")
        itype = ri.get("InstanceType", "")
        end = ri.get("End")
        days_left = -days_ago(end) if end else None
        active = state == "active"
        running_match = usage_is_complete(usage) and f"{region}:{itype}" in usage["instance_types"]

        if not active:
            flag = f"STALE (reservation expired: {state})"
        elif not usage_is_complete(usage) or running_match:
            flag = "ACTIVE"
        else:
            flag = "STALE (reserved instance, no matching running instance - likely wasted spend)"

        notes = (f"{ri.get('InstanceCount')}x {itype}, {ri.get('OfferingClass', '')}/"
                 f"{ri.get('OfferingType', '')}, {ri.get('ProductDescription', '')}. ")
        if active and days_left is not None:
            notes += f"Expires in {days_left} day(s). "
        if active and not running_match and usage is not None:
            notes += ("No running instance of this type found in this region - but RI coverage also "
                      "depends on platform, tenancy, AZ and size flexibility, so confirm in the "
                      "Billing console's reservation utilisation report before acting. ")
        if not active:
            notes += "Expired reservations cannot be deleted and stop costing money on their own. "

        rows.append(new_row(
            "ReservedInstance", region, ri.get("ReservedInstancesId", itype), itype,
            ri.get("Start"), end, days_ago(ri.get("Start")), True,
            flag, notes,
            tags=tags_to_dict(ri.get("Tags", [])),
            description=f"fixed ${ri.get('FixedPrice', 0):.2f}, usage ${ri.get('UsagePrice', 0):.4f}/hr, "
                        f"scope {ri.get('Scope', '')}",
        ))
        raw_capture.record("ReservedInstance", region,
                           ri.get("ReservedInstancesId", itype), ri)
    return rows
