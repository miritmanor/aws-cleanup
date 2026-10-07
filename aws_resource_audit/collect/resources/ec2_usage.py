"""The registry EC2's reference-counted types are judged by: producers record what
they reference, consumers ask whether anything does. Order matters - see EC2_USAGE_FLOW."""



# Ids are unique per type, so sets accumulate across regions; key pair names
# are only unique per region, so those are stored region-qualified.
def new_ec2_usage_registry():
    """What the producers saw. "complete" is False if a producer failed, and then an
    empty set means "nobody looked", not "nothing references it"."""
    return {"image_ids": set(), "key_names": set(), "sg_ids": set(),
            "snapshot_ids": set(), "active_ami_snapshot_ids": set(),
            "instance_types": set(), "vpc_ids": set(), "subnet_ids": set(),
            "launch_template_ids": set(), "complete": True}


def usage_is_complete(usage):
    """True only when every producer that feeds `usage` finished."""
    return bool(usage) and usage.get("complete", False)
