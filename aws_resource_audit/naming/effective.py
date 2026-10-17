"""Effective membership: the naming passes in their one correct order (reclaim, match,
manual overrides, merges). Called only by entry points: render, SnapshotStore, run."""

# Never import tagging.py here: it calls AWS, and render.py reaches this module.
# NoAwsFromTheRendererTests fails if it does.
from ..rows import member_key
from .group_names import (
    apply_manual_group_overrides,
    apply_assigned_names,
    group_project_id,
    load_group_store,
    reset_assigned_names,
)
from .merges import apply_merges, merge_root


def resolve_effective_membership(all_rows, groups_file):
    """Apply the saved group store to snapshot rows in place, setting project_id as
    well as the label. groups_file=None reclaims and applies nothing."""
    reset_assigned_names(all_rows)
    if not groups_file:
        return
    apply_assigned_names(all_rows, groups_file)
    apply_manual_group_overrides(all_rows, groups_file)
    apply_merges(all_rows, groups_file)


def remembered_empty_groups(all_rows, groups_file):
    """Saved groups with no member left in this scan, as (project_id, name): listed
    without a size. Here because it reads the store; analysis does no I/O."""
    if not groups_file:
        return ()
    live = {member_key(row) for row in all_rows}
    out = []
    groups = load_group_store(groups_file).get("groups", {})
    for gid, group in sorted(groups.items()):
        if merge_root(groups, gid) != gid:
            continue    # shown as part of the project it was merged into
        if not any(key in live for key in group.get("members", [])):
            out.append((group_project_id(gid), group.get("name") or gid))
    return tuple(out)
