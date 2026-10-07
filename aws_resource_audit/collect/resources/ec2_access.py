"""Security groups, key pairs and network interfaces: judged by whether anything
still references them (ec2_usage.py)."""

from ...config import RETRY_CONFIG
from ...staleness import days_ago
from ...collect.calls import paged, safe_call, tags_to_dict
from ...text import join_nonempty
from ...rows import add_edge, error_row, new_row
from ...collect import raw_capture
from .ec2_usage import usage_is_complete


def collect_security_groups(session, region, usage=None):
    """Security groups. "In use" means a scanned ENI or instance attaches it; a group
    referenced only by another group's rules still counts as unattached."""
    ec2 = session.client("ec2", region_name=region, config=RETRY_CONFIG)
    rows = []
    groups, page_error = paged(ec2, "describe_security_groups", "SecurityGroups", service="SecurityGroup")
    if page_error and not groups:
        return [error_row("SecurityGroup", region, "ERROR", page_error)]

    # An SG named in another SG's rules is in use in a way ENIs don't show.
    peer_referenced = set()
    for g in groups:
        for perm in list(g.get("IpPermissions", [])) + list(g.get("IpPermissionsEgress", [])):
            for pair in perm.get("UserIdGroupPairs", []):
                if pair.get("GroupId") and pair["GroupId"] != g["GroupId"]:
                    peer_referenced.add(pair["GroupId"])

    for g in groups:
        gid = g["GroupId"]
        gname = g.get("GroupName", "")
        attached = usage_is_complete(usage) and gid in usage["sg_ids"]
        is_default = gname == "default"

        if attached:
            flag = "ACTIVE (attached to a scanned ENI or instance)"
        elif is_default:
            flag = "ACTIVE (default group - cannot be deleted)"
        elif gid in peer_referenced:
            flag = "IN USE BY ANOTHER SECURITY GROUP"
        elif not usage_is_complete(usage):
            flag = "UNKNOWN"
        else:
            flag = "STALE (not attached to anything in this scan)"

        notes = "Security groups are free; removing unused ones is hygiene, not a saving. "
        if is_default:
            notes += "This is the VPC's default group - AWS does not allow deleting it. "
        if gid in peer_referenced:
            notes += "Referenced by another security group's rules, so it cannot be deleted until that rule goes. "
        if not attached and not is_default and usage is not None:
            notes += ("Not attached to any ENI or instance found in this scan. Services this script does "
                      "not scan (ECS, RDS, ElastiCache, VPC endpoints, ...) attach groups through ENIs, "
                      "which ARE scanned, so this is a reasonably strong signal - but confirm before deleting. ")

        row = new_row(
            "SecurityGroup", region, gid, gname,
            "", "", None, True, flag, notes,
            tags=tags_to_dict(g.get("Tags", [])),
            description=(g.get("Description", "") or "")[:70],
            connections=f"VPC={g.get('VpcId', '')}" if g.get("VpcId") else "",
            vpc_id=g.get("VpcId"), aws_default=is_default,
        )
        raw_capture.record("SecurityGroup", region, gid, g)
        rows.append(row)
    return rows


def collect_key_pairs(session, region, usage=None):
    """Key pairs: free, and only meaningful while an instance or template references
    one. Deleting one never affects a running instance."""
    ec2 = session.client("ec2", region_name=region, config=RETRY_CONFIG)
    resp = safe_call(ec2.describe_key_pairs)   # no paginator on this API
    if "__error__" in resp:
        return [error_row("KeyPair", region, "ERROR", resp["__error__"])]

    rows = []
    for kp in resp.get("KeyPairs", []):
        kname = kp["KeyName"]
        created = kp.get("CreateTime")
        in_use = usage_is_complete(usage) and f"{region}:{kname}" in usage["key_names"]
        rows.append(new_row(
            "KeyPair", region, kname, kname,
            created, "", days_ago(created), True,
            "ACTIVE (referenced by a scanned instance or launch template)" if in_use
            else ("UNKNOWN - the collectors that would reference it did not all run"
                  if not usage_is_complete(usage)
                  else "STALE (nothing in this scan references it)"),
            "Key pairs are free; this is cleanup for clarity, not cost. Deleting one does not lock you "
            "out of an already-running instance - the public key was written into the instance at launch "
            "and stays there. It only stops NEW launches from using that key name.",
            tags=tags_to_dict(kp.get("Tags", [])),
            description=f"{kp.get('KeyPairId', '')} {kp.get('KeyFingerprint', '')[:20]}",
        ))
        raw_capture.record("KeyPair", region, kname, kp)
    return rows


def collect_network_interfaces(session, region, usage=None):
    """ENIs: the authoritative record of which security groups are in effect. Requester-
    managed ENIs belong to a service; one with a public IPv4 is billed."""
    ec2 = session.client("ec2", region_name=region, config=RETRY_CONFIG)
    rows = []
    enis, page_error = paged(ec2, "describe_network_interfaces", "NetworkInterfaces",
                             service="NetworkInterface")
    if page_error and usage is not None:
        usage["complete"] = False
    if page_error and not enis:
        return [error_row("NetworkInterface", region, "ERROR", page_error)]

    for eni in enis:
        eid = eni["NetworkInterfaceId"]
        status = eni.get("Status", "")
        attachment = eni.get("Attachment", {}) or {}
        instance_id = attachment.get("InstanceId")
        managed = eni.get("RequesterManaged", False)
        assoc = eni.get("Association", {}) or {}
        public_ip = assoc.get("PublicIp")
        # NOTE: TagSet, not Tags - this is the one EC2 describe call that
        # names the field differently.
        tags = tags_to_dict(eni.get("TagSet", []))

        if status == "in-use":
            flag = "ACTIVE"
        elif managed:
            flag = "AVAILABLE (AWS service-managed - delete the owning service, not this)"
        else:
            flag = "STALE (detached)"

        notes = f"Interface type: {eni.get('InterfaceType', 'interface')}. "
        if managed:
            notes += (f"Managed by an AWS service (requester {eni.get('RequesterId', 'unknown')}) - it "
                      "reappears if deleted directly; remove the service that owns it instead. ")
        if public_ip:
            notes += (f"Carries public IPv4 {public_ip} - AWS bills every public IPv4 address hourly, "
                      "so this is not free even while detached. ")
        if status != "in-use" and not managed:
            notes += "Detached ENIs are free but can block deleting their security groups or subnet. "

        row = new_row(
            "NetworkInterface", region, eid, eni.get("PrivateIpAddress", ""),
            "", "", None, True, flag, notes, tags=tags,
            description=(eni.get("Description", "") or "")[:70],
            connections=join_nonempty([f"VPC={eni.get('VpcId', '')}", f"subnet={eni.get('SubnetId', '')}"]),
            vpc_id=eni.get("VpcId"),
            cost_hint="unattached" if (status != "in-use" and not managed and not public_ip) else None,
        )
        raw_capture.record("NetworkInterface", region, eid, eni)
        add_edge(row, instance_id, "attached to", "ENI Attachment.InstanceId",
                 conn_type="eni.ec2.attachment", target_service="EC2Instance")
        add_edge(row, eni.get("SubnetId"), "is in subnet", "ENI SubnetId",
                 conn_type="eni.subnet.placement", target_service="Subnet")
        if usage is not None:
            # Every VPC-attached resource has an ENI, so these say which VPCs and subnets hold anything.
            usage["vpc_ids"].update(filter(None, [eni.get("VpcId")]))
            usage["subnet_ids"].update(filter(None, [eni.get("SubnetId")]))
        add_edge(row, assoc.get("AllocationId"), "carries Elastic IP", "ENI Association.AllocationId",
                 conn_type="eni.elasticip.association", target_service="ElasticIP")
        for grp in eni.get("Groups", []):
            add_edge(row, grp.get("GroupId"), "uses security group", "ENI Groups",
                     conn_type="eni.securitygroup.membership", target_service="SecurityGroup")
            if usage is not None and grp.get("GroupId"):
                usage["sg_ids"].add(grp["GroupId"])
        rows.append(row)
    return rows
