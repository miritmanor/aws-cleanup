"""Which project owns a resource. Anchors (tags, name rules) are stated; OWNS edges assign;
SUPPORTING edges assign one hop only when one project uses it; nothing else assigns."""

from ..config import (
    COMPONENT_TAG_KEYS,
    STRUCTURAL_SERVICES,
    ENVIRONMENT_TAG_KEYS,
    PROJECT_TAG_KEYS,
)
from ..console import say
from ..connection_types.vocabulary import OWNERSHIP_OWNS, OWNERSHIP_SUPPORTING
from ..registry import CONNECTION_TYPES_BY_ID, feeds_grouping
from .name_tokens import build_name_token_index, owner_name_tokens

# Membership, as reported. "assigned" is the old "grouped"; the other three
# are answers the previous model had no way to give.
ASSIGNED = "assigned"
SHARED = "shared"
AMBIGUOUS = "ambiguous"
UNASSIGNED = "unassigned"


def _first_tag(tags, keys):
    for key in keys:
        value = (tags or {}).get(key)
        if value and value.strip():
            return value.strip()
    return ""


def _project_tags(tags):
    """Every DISTINCT project tag value on one resource; more than one is a finding."""
    seen = []
    for key in PROJECT_TAG_KEYS:
        value = (tags or {}).get(key)
        value = value.strip() if value else ""
        if value and value not in seen:
            seen.append(value)
    return seen


def find_group_signal(tags_dict):
    """The project tag value, or "". Project tags only, never Environment."""
    return _first_tag(tags_dict, PROJECT_TAG_KEYS)


class Projects:
    """The projects this scan knows, by stable id saying what established each
    ("tag:orders", "rule:shop"), so ids survive renames and scan order."""

    def __init__(self):
        self._names = {}

    def declare(self, project_id, display_name):
        self._names.setdefault(project_id, display_name)
        return project_id

    def from_tag(self, value):
        return self.declare(f"tag:{value}", value)

    def from_rule(self, word, display):
        return self.declare(f"rule:{word}", display or word)

    def from_deployment(self, row):
        """An unnamed project held together by a deployment, keyed on one member's
        canonical key so the id survives the account growing."""
        return self.declare(f"deployment:{row['resource_key']}",
                            row.get("name") or row["resource_id"])

    def name(self, project_id):
        return self._names.get(project_id, "")


def _naming_member(row):
    """Which member names a deployment group: structural resources first, then chosen
    names over AWS-generated ids, then the canonical key for determinism."""
    name = (row.get("name") or "").strip()
    has_real_name = bool(name) and name != row.get("resource_id")
    return (0 if row["service"] in STRUCTURAL_SERVICES else 1,
            0 if has_real_name else 1,
            row["resource_key"])


def _anchor_rows(all_rows, projects, name_rules, notes):
    """Each row's stated project, before inference. A name rule outranks a tag; the
    contradiction is recorded either way."""
    anchors = {}
    origins = {}
    for idx, row in enumerate(all_rows):
        values = _project_tags(row.get("tags"))
        if row.get("_group_key") and row["_group_key"] not in values:
            values = [row["_group_key"]] + values
        if len(values) > 1:
            notes["tag_conflicts"].append(
                f"{row['service']}:{row['resource_id']} carries more than one project "
                f"tag ({', '.join(values)}) - using {values[0]!r}")
        if values:
            anchors[idx] = projects.from_tag(values[0])
            origins[idx] = "tag"

        for word, display in (name_rules or {}).items():
            if word.lower() in (row.get("name") or "").lower():
                rule_project = projects.from_rule(word, display)
                if idx in anchors and anchors[idx] != rule_project:
                    notes["conflicts"].append(
                        f"{row['service']}:{row['resource_id']} is tagged "
                        f"{projects.name(anchors[idx])!r} but matches the name rule "
                        f"{word!r} - the rule wins; remove it or retag the resource")
                anchors[idx] = rule_project
                origins[idx] = "rule"
                break
    return anchors, origins


def _edges_by_ownership(edge_links):
    """Resolved edges split into owning and supporting, by what they may claim."""
    owning, supporting = [], []
    for link in edge_links or []:
        src, tgt, reason = link[0], link[1], link[2]
        conn_type = link[3] if len(link) > 3 else None
        conn = CONNECTION_TYPES_BY_ID.get(conn_type)
        level = conn.ownership if conn else None
        if level == OWNERSHIP_OWNS:
            owning.append((src, tgt, reason))
        elif level == OWNERSHIP_SUPPORTING:
            supporting.append((src, tgt, reason))
    return owning, supporting


def _seed_from_ownership(all_rows, owning, anchors, reasons, notes):
    """Union rows tied by containment or deployment. A merge between two DIFFERENT
    anchored projects is refused and recorded."""
    parent = list(range(len(all_rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def anchor_of(root):
        for idx, project in anchors.items():
            if find(idx) == root:
                return project
        return None

    for src, tgt, reason in sorted(owning):
        rs, rt = find(src), find(tgt)
        if rs == rt:
            continue
        a, b = anchor_of(rs), anchor_of(rt)
        if a and b and a != b:
            notes["conflicts"].append(
                f"{all_rows[src]['service']}:{all_rows[src]['resource_id']} and "
                f"{all_rows[tgt]['service']}:{all_rows[tgt]['resource_id']} are linked "
                f"({reason}) but belong to different projects - not merged")
            continue
        parent[rs] = rt
        for idx in (src, tgt):
            reasons.setdefault(idx, []).append(reason)
    return find


def _consumers_of(supporting, assigned, anchors):
    """Which assigned projects use each unassigned resource. Single hop: no transitivity."""
    consumers = {}
    for src, tgt, reason in supporting:
        for a, b in ((src, tgt), (tgt, src)):
            project = assigned.get(a)
            if project and b not in anchors and b not in assigned:
                consumers.setdefault(b, {}).setdefault(project, []).append(reason)
    return consumers


def apply_project_grouping(all_rows, edge_links=None, default_vpc_ids=None,
                           default_vpcs_known=True, name_rules=None):
    """Assign each row a project, or say why it has none (project_id, label, membership,
    method, why, shared_with, ...). default_vpc_* are accepted and ignored."""
    projects = Projects()
    notes = {"conflicts": [], "tag_conflicts": [], "candidate_words": [],
             "grouping_links": [], "shared": []}
    reasons = {}

    for row in all_rows:
        if not row.get("_group_key"):
            row["_group_key"] = find_group_signal(row.get("tags") or {})
        row["environment"] = _first_tag(row.get("tags"), ENVIRONMENT_TAG_KEYS)
        row["component"] = _first_tag(row.get("tags"), COMPONENT_TAG_KEYS)

    anchors, origins = _anchor_rows(all_rows, projects, name_rules, notes)
    if not feeds_grouping("group.shared-tag"):
        # The tag mechanism switched off: keep only what the user wrote as a
        # rule, so turning off tag grouping cannot also discard name rules.
        anchors = {i: p for i, p in anchors.items() if origins.get(i) == "rule"}

    owning, supporting = _edges_by_ownership(edge_links)
    find = _seed_from_ownership(all_rows, owning, anchors, reasons, notes)

    assigned = dict(anchors)
    by_root = {}
    for idx in range(len(all_rows)):
        by_root.setdefault(find(idx), []).append(idx)
    for members in by_root.values():
        anchored = {anchors[i] for i in members if i in anchors}
        if len(anchored) == 1:
            project = next(iter(anchored))
            origin = "inferred"
        elif not anchored and len(members) > 1:
            # Held together by containment, named by nobody; any member is a stable id.
            owner = min((all_rows[i] for i in members), key=_naming_member)
            project = projects.from_deployment(owner)
            origin = "deployment"
        else:
            continue
        for i in members:
            if i not in assigned:
                assigned[i] = project
                origins[i] = origin

    consumers = _consumers_of(supporting, assigned, anchors)

    for idx, row in enumerate(all_rows):
        project = assigned.get(idx)
        candidates = consumers.get(idx, {})
        membership = UNASSIGNED
        shared_with = []

        if project:
            membership = ASSIGNED
        elif len(candidates) == 1:
            project = next(iter(candidates))
            origins[idx] = "inferred"
            reasons.setdefault(idx, []).extend(candidates[project][:2])
            membership = ASSIGNED
        elif len(candidates) > 1:
            # Several projects use it: shared, not a tie to break.
            membership = SHARED
            shared_with = sorted(candidates)
            notes["shared"].append(
                f"{row['service']}:{row['resource_id']} is used by "
                f"{len(shared_with)} projects: {', '.join(shared_with)}")

        row["project_id"] = project or ""
        row["_detected_project_id"] = row["project_id"]
        row["project_group"] = projects.name(project) if project else ""
        row["membership"] = membership
        row["shared_with"] = shared_with
        row["project_candidates"] = sorted(candidates)
        row["grouping_method"] = (origins.get(idx, "inferred") if project
                                  else membership)
        row["_detected_project_group"] = row["project_group"]
        row["_detected_grouping_method"] = row["grouping_method"]
        why = list(dict.fromkeys(reasons.get(idx, [])))
        row["why_grouped"] = " | ".join(why) if why else (
            "carries its own project tag" if origins.get(idx) == "tag" else
            "matched a name rule you configured" if origins.get(idx) == "rule"
            else "")

    notes["grouping_links"] = _tag_star_links(all_rows)
    notes["candidate_words"] = _candidate_words(all_rows)
    _report(all_rows, notes)
    return notes


def _tag_star_links(all_rows):
    """Draw a tag-formed project as connected: a star from its first member, since a
    shared tag creates no edges of its own."""
    by_project = {}
    for idx, row in enumerate(all_rows):
        if row.get("grouping_method") == "tag" and row.get("project_id"):
            by_project.setdefault(row["project_id"], []).append(idx)
    links = []
    for project, members in sorted(by_project.items()):
        if len(members) < 2:
            continue
        hub = members[0]
        for other in members[1:]:
            links.append({
                "source_idx": hub, "target_idx": other,
                "reason": f"shares project tag '{all_rows[hub]['project_group']}'",
                "conn_type": "group.shared-tag",
            })
    return links


def _candidate_words(all_rows):
    """Words worth writing a name rule for: only those whose holders are not already
    in one project. Discovery only; nothing here groups anything."""
    index = build_name_token_index([r.get("name") or "" for r in all_rows])
    owners = owner_name_tokens(all_rows)
    out = []
    for word, holders in sorted(index.items()):
        if len(holders) < 2 or word in owners:
            continue
        hit = {all_rows[i].get("project_id") or "" for i in holders}
        if hit == {""} or len(hit) > 1:
            out.append({
                "word": word,
                "resources": sorted(
                    f"{all_rows[i]['service']}:{all_rows[i]['resource_id']}"
                    for i in holders),
            })
    return out


def _report(all_rows, notes):
    counts = {}
    for row in all_rows:
        counts[row["membership"]] = counts.get(row["membership"], 0) + 1
    say("Projects: " + ", ".join(f"{n} {state}" for state, n in sorted(counts.items())))
    if notes["tag_conflicts"]:
        say(f"  {len(notes['tag_conflicts'])} resource(s) carry conflicting project tags")
    if notes["conflicts"]:
        say(f"  {len(notes['conflicts'])} link(s) crossed a project boundary and were "
            "NOT merged")
    if notes["shared"]:
        say(f"  {len(notes['shared'])} resource(s) are used by more than one project, "
            "and are reported as shared rather than assigned to one")
    if notes["candidate_words"]:
        words = ", ".join(w["word"] for w in notes["candidate_words"][:4])
        say(f"  {len(notes['candidate_words'])} name(s) could group resources that "
            f"nothing else connects ({words}, ...)")
