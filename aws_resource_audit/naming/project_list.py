"""Every project with its name, source and saved-name state, for the Projects tab.
Keyed on project_id. Reads the store, so only entry points call it."""

from ..rows import member_key
from .group_names import GROUP_ID_PREFIX, OVERLAP_PERCENT, load_group_store

SOURCE_TAG = "AWS tag"
SOURCE_RULE = "name rule"
SOURCE_DEPLOYMENT = "deployment"
SOURCE_HAND_PICKED = "hand-picked"

_SOURCE_BY_PREFIX = {"tag": SOURCE_TAG, "rule": SOURCE_RULE, "deployment": SOURCE_DEPLOYMENT}

NAME_DEFAULT = "default"
NAME_ASSIGNED = "assigned"


def _source_of(row, manual):
    if manual:
        return SOURCE_HAND_PICKED
    detected = row.get("_detected_project_id") or row.get("project_id") or ""
    return _SOURCE_BY_PREFIX.get(detected.split(":", 1)[0], "")


def _entry(project_id, name, rows, gid=None, saved=None, live=frozenset()):
    saved = saved or {}
    manual = bool(saved.get("manual"))
    saved_members = saved.get("members", [])
    live_members = sum(1 for k in saved_members if k in live)
    not_applied = ""
    if gid and live_members and not rows:
        not_applied = (f"No project holds {OVERLAP_PERCENT}% of its saved resources "
                       "any more, so the name is not shown anywhere.")
    return {
        "project_id": project_id,
        "name": name,
        "name_kind": NAME_ASSIGNED if gid else NAME_DEFAULT,
        "sources": sorted({s for s in (_source_of(r, manual) for r in rows) if s}),
        "resource_count": len(rows),
        "group_id": gid,
        "seed_key": member_key(rows[0]) if rows and not gid else None,
        "manual": manual,
        "saved_members": len(saved_members),
        "live_members": live_members,
        "updated_at": saved.get("updated_at"),
        "not_applied": not_applied,
    }


def project_list(all_rows, groups_file):
    """One entry per project, plus saved names that claimed none; largest first.
    Expects rows already resolved by resolve_effective_membership."""
    groups = load_group_store(groups_file).get("groups", {}) if groups_file else {}
    live = {member_key(r) for r in all_rows}
    by_project = {}
    for row in all_rows:
        if row.get("project_id"):
            by_project.setdefault(row["project_id"], []).append(row)

    out = []
    for pid, rows in by_project.items():
        gid = pid[len(GROUP_ID_PREFIX):] if pid.startswith(GROUP_ID_PREFIX) else None
        saved = groups.get(gid) if gid else None
        name = (saved or {}).get("name") or rows[0].get("project_group") or pid
        out.append(_entry(pid, name, rows, gid if saved else None, saved, live))
    for gid, saved in groups.items():
        pid = GROUP_ID_PREFIX + gid
        if pid not in by_project:
            out.append(_entry(pid, saved.get("name") or gid, [], gid, saved, live))
    return sorted(out, key=lambda p: (-p["resource_count"], p["name"].lower(), p["project_id"]))
