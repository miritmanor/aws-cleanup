"""Grouping reviewing itself: oversized or weakly-evidenced clusters, refused conflicts,
out-of-scan references. Returns findings; present/markdown.py writes them."""

from collections import namedtuple

from ..config import NAME_TOKEN_MAX_SHARE

WEAK_EVIDENCE_MARKERS = ("config reference", "LOW CONFIDENCE name-match heuristic")

LARGE_CLUSTER_FRACTION = 0.15

# Everything present/markdown.py needs to write the report, and nothing about
# how it should read.
Findings = namedtuple("Findings", [
    "total",              # rows scanned
    "cluster_count",      # named or inferred groups
    "large_clusters",     # [(name, members, fraction_of_account)]
    "weak_only_clusters", # [(name, members)]
    "collisions",         # rows whose id matched the wrong type
    "dangling",           # rows referencing something outside the scan
    # What the constrained assignment refused, and what it noticed.
    "conflicts",          # links that crossed a project boundary, not merged
    "tag_conflicts",      # one resource carrying two project tags
    "shared",             # resources used by more than one project
    "candidate_words",    # words a name rule could act on
])


def _has_kind(row, kind):
    return any(ref["kind"] == kind for ref in row.get("_references", ()))


def _cluster_names(clusters):
    """{project_id: display name}: the label, plus the id when two projects share it."""
    labels = {pid: members[0].get("project_group") or pid for pid, members in clusters.items()}
    taken = list(labels.values())
    return {pid: f"{label} ({pid})" if taken.count(label) > 1 else label
            for pid, label in labels.items()}


def audit_groups(all_rows, notes=None):
    notes = notes or {}
    clusters = {}
    for row in all_rows:
        project = row.get("project_id")
        if project:
            clusters.setdefault(project, []).append(row)
    names = _cluster_names(clusters)

    total = len(all_rows)
    large, weak_only = [], []
    for project, members in clusters.items():
        name = names[project]
        fraction = (len(members) / total) if total else 0
        if fraction >= LARGE_CLUSTER_FRACTION and len(members) > 3:
            large.append((name, members, fraction))

        reasons = [seg for m in members
                   for seg in (m.get("why_grouped") or "").split(" | ") if seg]
        strong = any(not any(w in reason for w in WEAK_EVIDENCE_MARKERS)
                     for reason in reasons)
        if reasons and not strong:
            weak_only.append((name, members))

    return Findings(
        total=total,
        cluster_count=len(clusters),
        large_clusters=sorted(large, key=lambda item: -item[2]),
        weak_only_clusters=weak_only,
        collisions=[r for r in all_rows if _has_kind(r, "collision")],
        dangling=[r for r in all_rows if _has_kind(r, "dangling")],
        conflicts=notes.get("conflicts") or [],
        tag_conflicts=notes.get("tag_conflicts") or [],
        shared=notes.get("shared") or [],
        candidate_words=notes.get("candidate_words") or [],
    )
