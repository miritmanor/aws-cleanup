"""Merging one project into another. The merge is saved on the absorbed project's saved
name (`merged_into`), re-applied on every load, and undone by forgetting the target."""

from ..config import now
from ..present.format import fmt_dt
from ..rows import member_key
from .group_names import GROUP_ID_PREFIX, claim_row, listed_keys, load_group_store


class EditRefused(Exception):
    """A merge or addition the store must not record. `conflict` marks a clash with
    another saved name, as opposed to a request that can never succeed."""

    def __init__(self, message, conflict=False):
        super().__init__(message)
        self.conflict = conflict


def merge_root(groups, gid):
    """The saved name `gid` is shown under: itself, or the end of its merged_into chain.
    A missing target or a cycle stops the walk."""
    seen = {gid}
    while True:
        target = groups.get(gid, {}).get("merged_into")
        if not target or target not in groups or target in seen:
            return gid
        seen.add(target)
        gid = target


def absorbed_names(groups, gid):
    """Names of the saved names merged into `gid`, directly or through a chain."""
    return sorted((g.get("name") or other) for other, g in groups.items()
                  if other != gid and merge_root(groups, other) == gid)


def apply_merges(all_rows, groups_file):
    """Re-stamp every row of an absorbed saved name with its target's id and name. Must
    run AFTER the other naming passes; never writes the store back."""
    if not groups_file:
        return
    groups = load_group_store(groups_file).get("groups", {})
    if not any(g.get("merged_into") for g in groups.values()):
        return
    for row in all_rows:
        pid = row.get("project_id") or ""
        if not pid.startswith(GROUP_ID_PREFIX):
            continue
        gid = pid[len(GROUP_ID_PREFIX):]
        root = merge_root(groups, gid)
        if root != gid:
            claim_row(row, root, groups[root].get("name") or root)


def is_tag_derived(row):
    return (row.get("_detected_grouping_method") or row.get("grouping_method")) == "tag"


def saved_gid(groups, pid, rows, next_gid, merge_only):
    """The saved name behind project `pid`, created from all its resources if the
    project only has a default name."""
    if pid.startswith(GROUP_ID_PREFIX) and pid[len(GROUP_ID_PREFIX):] in groups:
        return pid[len(GROUP_ID_PREFIX):]
    owned = {k for g in groups.values() for k in listed_keys(g)}
    keys = sorted({member_key(r) for r in rows} - owned)
    name = rows[0].get("project_group") or pid
    if not keys:
        raise EditRefused(f"Every resource in \"{name}\" is already listed under another "
                          "saved name. Forget that name first.", conflict=True)
    gid = next_gid()
    groups[gid] = {"name": name, "members": keys, "updated_at": fmt_dt(now())}
    if merge_only:
        # Saved only to record the merge: forgetting the target deletes it again.
        groups[gid]["merge_only"] = True
    return gid


def merge_projects(store, rows, source_pid, target_pid, next_gid):
    """Merge project `source_pid` into `target_pid` in `store`; returns the target's gid.
    `rows` must be resolved by resolve_effective_membership. Raises EditRefused."""
    if source_pid == target_pid:
        raise EditRefused("A project cannot be merged into itself.")
    by_pid = {}
    for row in rows:
        if row.get("project_id") in (source_pid, target_pid):
            by_pid.setdefault(row["project_id"], []).append(row)
    for pid in (source_pid, target_pid):
        if pid not in by_pid:
            raise EditRefused(f"No project {pid} has resources in the current scan.")

    source_rows = by_pid[source_pid]
    tagged = sorted(member_key(r) for r in source_rows if is_tag_derived(r))
    if tagged:
        raise EditRefused(
            f"{', '.join(tagged)} carr{'y' if len(tagged) > 1 else 'ies'} a real AWS "
            "Project tag - edit the tag in AWS to change its project, a merge can't "
            "override it.")

    groups = store.setdefault("groups", {})
    source_gid = saved_gid(groups, source_pid, source_rows, next_gid, merge_only=True)
    target_gid = saved_gid(groups, target_pid, by_pid[target_pid], next_gid, merge_only=False)
    if merge_root(groups, target_gid) == source_gid:
        raise EditRefused("That project is already merged into this one.")

    groups[source_gid]["merged_into"] = target_gid
    groups[source_gid]["updated_at"] = groups[target_gid]["updated_at"] = fmt_dt(now())
    return target_gid


def forget_group(store, gid):
    """Delete saved name `gid` and split apart what was merged into it: an absorbed name
    comes back, and one saved only to record the merge is deleted too."""
    groups = store.setdefault("groups", {})
    groups.pop(gid, None)
    for other in sorted(g for g, s in groups.items() if s.get("merged_into") == gid):
        if other not in groups:
            continue
        if groups[other].get("merge_only"):
            forget_group(store, other)
        else:
            groups[other].pop("merged_into", None)
