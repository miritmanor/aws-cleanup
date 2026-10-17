"""Adding hand-picked resources to a project. A project that grows on its own keeps
growing: the picks are saved as extra_members, beside the members it grows from."""

from ..config import now
from ..present.format import fmt_dt
from ..rows import member_key
from .group_names import GROUP_ID_PREFIX, listed_keys
from .merges import EditRefused, is_tag_derived, saved_gid

NOT_IN_SCAN_HINT = ("A member key looks like '<service>:<region>:<resource_id>' - copy "
                    "one from the table.")


# Never move a resource from another saved name unasked. A tagged one is refused: a
# real AWS tag is account state, and the next scan would contradict the pick.
def check_addable(groups, rows, keys, exclude_gid=None):
    """Raise EditRefused if any of `keys` is listed by another saved name, missing from
    the scan, or in a project by its own AWS tag. Empty `rows` skips the scan checks."""
    conflicts = []
    for key in keys:
        for gid, group in sorted(groups.items()):
            if gid != exclude_gid and key in listed_keys(group):
                conflicts.append(f"{key} is already in group {gid} (\"{group.get('name')}\")")
                break
    if conflicts:
        raise EditRefused("; ".join(conflicts) + ". Rename or remove it from that group "
                          "first, or drop it from this selection.", conflict=True)

    live = {member_key(r) for r in rows}
    missing = [k for k in keys if live and k not in live]
    if missing:
        raise EditRefused(f"not in the current scan: {', '.join(missing)}. {NOT_IN_SCAN_HINT}")

    tagged = sorted({member_key(r) for r in rows if is_tag_derived(r)} & set(keys))
    if tagged:
        raise EditRefused(
            f"{', '.join(tagged)} already carr{'y' if len(tagged) > 1 else 'ies'} a real AWS "
            "Project tag - edit the tag in AWS to change its group, manual grouping can't "
            "override it.")


def add_to_group(groups, gid, keys):
    """Add `keys` to saved name `gid`: to a manual group's members, otherwise to its
    extra_members, so a group that grows on its own keeps growing."""
    group = groups[gid]
    field = "members" if group.get("manual") else "extra_members"
    new = set(keys) - set(listed_keys(group))
    group[field] = sorted(set(group.get(field, [])) | new)
    group["updated_at"] = fmt_dt(now())


def add_to_project(store, rows, project_id, keys, next_gid):
    """Add `keys` to project `project_id`, saving its current name first if it only has a
    default one. `rows` must be resolved; returns the gid. Raises EditRefused."""
    groups = store.setdefault("groups", {})
    project_rows = [r for r in rows if r.get("project_id") == project_id]
    if not project_rows:
        raise EditRefused(f"No project {project_id} has resources in the current scan.")
    keys = sorted(set(keys))
    existing = project_id[len(GROUP_ID_PREFIX):] if project_id.startswith(GROUP_ID_PREFIX) else None
    check_addable(groups, rows, keys, exclude_gid=existing)

    gid = saved_gid(groups, project_id, project_rows, next_gid, merge_only=False)
    add_to_group(groups, gid, keys)
    return gid
