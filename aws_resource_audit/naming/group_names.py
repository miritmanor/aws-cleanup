"""Saved project names, stored against the project's resources (member_key) and
matched by overlap on each load. Nothing here touches AWS."""

import json
import os

from ..config import now
from ..present.format import fmt_dt
from ..rows import member_key
from ..console import say
from ..scope import scan_account
from ..text import join_nonempty


# Bumped when the file's meaning changes. Version 1 is the first to record
# which account it describes.
GROUP_STORE_VERSION = 1


def load_group_store(path):
    """The saved group names, or an empty store. A store from another account is
    refused, since member keys carry no account; one with no account is trusted."""
    if not path or not os.path.exists(path):
        return {"groups": {}}
    try:
        with open(path) as f:
            data = json.load(f)
            data.setdefault("groups", {})
    except (json.JSONDecodeError, OSError) as e:
        say(f"  Could not read groups file {path} ({e}) - starting a fresh one (old file left untouched).")
        return {"groups": {}}

    stored_account = data.get("account") or ""
    current = scan_account()
    if stored_account and current and stored_account != current:
        say(f"  Groups file {path} was written for account {stored_account}, but this "
            f"scan is of {current} - ignoring it. Its resource keys carry no account, "
            "so applying them here would attach one account's group names to another "
            "account's resources. Point 'output_dir' somewhere per-account to keep both.")
        return {"groups": {}}
    return data


def save_group_store(path, store):
    # Stamped on every write, so a store gains the account the first time
    # anything saves it, without a migration step.
    store.setdefault("schema_version", GROUP_STORE_VERSION)
    if scan_account():
        store["account"] = scan_account()
    with open(path, "w") as f:
        json.dump(store, f, indent=2)


def load_name_rules(path):
    """The word -> project-name rules from the store; empty by default."""
    store = load_group_store(path)
    rules = store.get("name_rules") or {}
    return {str(word).strip(): str(name).strip()
            for word, name in rules.items() if str(word).strip()}


OVERLAP_THRESHOLD = 0.4
OVERLAP_PERCENT = round(OVERLAP_THRESHOLD * 100)
GROUP_ID_PREFIX = "group:"

MANUAL_WHY = "manually grouped"
DETECTED_PREFIX = "originally: "


def group_project_id(gid):
    """The project_id a saved group gives every row it claims."""
    return f"{GROUP_ID_PREFIX}{gid}"


def _claim(row, gid, name):
    """Stamp one row as a member of saved group `gid`: its name AND its ID."""
    row["project_group"] = name
    row["project_id"] = group_project_id(gid)
    row["grouping_method"] = "named"


def _mark_manual(why):
    """Prepend the manual marker, keeping what the scan detected. Idempotent."""
    segments = [s for s in (why or "").split(" | ") if s and s != MANUAL_WHY]
    if segments and segments[0].startswith(DETECTED_PREFIX):
        segments[0] = segments[0][len(DETECTED_PREFIX):]
    detected = " | ".join(segments)
    return join_nonempty([MANUAL_WHY, DETECTED_PREFIX + detected if detected else ""], " | ")


def reset_assigned_names(all_rows):
    """Restore what grouping detected (id, default name, method) so the store can be
    applied again to snapshot rows."""
    for row in all_rows:
        if "_detected_project_id" in row:
            row["project_id"] = row["_detected_project_id"]
        if "_detected_project_group" in row:
            row["project_group"] = row["_detected_project_group"]
        if row.get("grouping_method") == "named":
            row["grouping_method"] = row.get("_detected_grouping_method") or "inferred"


def apply_assigned_names(all_rows, groups_file):
    """Re-apply saved names by RESOURCE IDENTITY: a project gets a name when it holds
    most still-present saved members (min 2). Splits keep the name; membership only grows."""
    if not groups_file:
        return
    store = load_group_store(groups_file)
    groups = store["groups"]

    projects = {}
    for r in all_rows:
        pid = r.get("project_id")
        if pid and not pid.startswith(GROUP_ID_PREFIX):
            projects.setdefault(pid, []).append(r)
    all_current_keys = {member_key(r) for r in all_rows}

    # Iterating saved names (not projects) is what makes a split detectable:
    # one saved name can legitimately claim several of this run's projects.
    candidates = {}   # project_id -> [(gid, score), ...] every qualifying name
    unmatched = []
    for gid, g in sorted(groups.items()):
        if g.get("manual"):
            # Hand-picked: apply_manual_group_overrides applies these afterwards.
            continue
        stored_present = set(g.get("members", [])) & all_current_keys
        if not stored_present:
            continue
        matched = False
        for pid, rows_in_project in sorted(projects.items()):
            overlap = len({member_key(r) for r in rows_in_project} & stored_present)
            if not overlap or (overlap < 2 and len(stored_present) > 1):
                continue
            score = overlap / len(stored_present)
            if score < OVERLAP_THRESHOLD:
                continue
            candidates.setdefault(pid, []).append((gid, score))
            matched = True
        if not matched:
            unmatched.append(g.get("name") or gid)

    # Highest score wins a contested project; ties break on gid so the outcome
    # is identical run to run rather than depending on dict ordering.
    claimed = {pid: sorted(cands, key=lambda c: (-c[1], c[0]))[0]
               for pid, cands in candidates.items()}

    # A resource another saved name already lists is NOT absorbed into the
    # winner's list, or one merge would entangle the two lists for good.
    owned_elsewhere = {}
    for gid, g in groups.items():
        for k in g.get("members", []):
            owned_elsewhere.setdefault(k, set()).add(gid)

    by_gid = {}
    for pid, (gid, _score) in claimed.items():
        by_gid.setdefault(gid, []).append(pid)

    for gid, pids in sorted(by_gid.items()):
        name = groups[gid]["name"]
        gained = set()
        for pid in pids:
            for r in projects[pid]:
                _claim(r, gid, name)
                k = member_key(r)
                if owned_elsewhere.get(k, {gid}) <= {gid}:
                    gained.add(k)
        # Extend, never replace - see docstring.
        groups[gid]["members"] = sorted(set(groups[gid].get("members", [])) | gained)
        groups[gid]["updated_at"] = fmt_dt(now())
        if len(pids) > 1:
            say(f"  Project \"{name}\" split into {len(pids)} parts - the link that joined them is gone")

    for pid, cands in sorted(candidates.items()):
        if len(cands) < 2:
            continue
        winner_gid = claimed[pid][0]
        others = ", ".join(f"\"{groups[g]['name']}\"" for g, s in cands if g != winner_gid)
        say(f"  Project \"{groups[winner_gid]['name']}\" merged with project {others} - "
            "now joined by a new link, shown under one name")

    for name in unmatched:
        say(f"  Saved name \"{name}\" not applied: no project holds {OVERLAP_PERCENT}% of its resources")

    save_group_store(groups_file, store)

    default_named = len(projects) - len(claimed)
    if default_named:
        assigned = len(by_gid)
        say(f"  {default_named} project(s) with a default name"
            + (f", {assigned} with an assigned name" if assigned else ""))


def apply_manual_group_overrides(all_rows, groups_file):
    """Force a hand-assembled ("manual") group's exact membership onto the rows. Must
    run AFTER apply_assigned_names; never writes the store back."""
    if not groups_file:
        return
    store = load_group_store(groups_file)
    by_key = {member_key(r): r for r in all_rows}

    for gid, g in sorted(store.get("groups", {}).items()):
        if not g.get("manual"):
            continue
        name = g["name"]
        for key in g.get("members", []):
            row = by_key.get(key)
            if row is None:
                continue    # not in this scan - stored membership outlives a scan
            _claim(row, gid, name)
            row["why_grouped"] = _mark_manual(row.get("why_grouped"))
