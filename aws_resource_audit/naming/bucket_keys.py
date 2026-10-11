"""Saved member keys for S3 buckets, moved to the region the bucket is recorded in now.
Buckets were once recorded as "global"; see docs/decisions/0062."""

from ..rows import member_key

BUCKET_SERVICE = "S3Bucket"


def rekey_moved_buckets(store, all_rows):
    """Rewrite each stored S3Bucket key to the current row's key, matched by bucket name
    (unique across AWS). Works both ways, so an old snapshot still finds its names."""
    current = {r["resource_id"]: member_key(r) for r in all_rows
               if r.get("service") == BUCKET_SERVICE}
    for group in store.get("groups", {}).values():
        members = group.get("members", [])
        moved = [_current_key(k, current) for k in members]
        if moved != members:
            group["members"] = sorted(set(moved))


def _current_key(key, current):
    service, _, rest = key.partition(":")
    if service != BUCKET_SERVICE:
        return key
    _region, _, name = rest.partition(":")
    return current.get(name, key)
