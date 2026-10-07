"""One summary per project and inventory bucket for the overview: size and unused share,
keyed on project_id (never the label). No cost; a summary is not a verdict."""

from dataclasses import dataclass

from ..rows import member_key
from ..staleness import USAGE_ACTIVE, USAGE_UNKNOWN, USAGE_UNUSED
from .grouping import AMBIGUOUS, SHARED, UNASSIGNED

# Bumped when the shape changes; versioned apart from the snapshot.
SUMMARY_SCHEMA_VERSION = 2

# A project, or an inventory bucket that counts each resource exactly once.
KIND_PROJECT = "project"
KIND_SHARED = "shared"
KIND_AMBIGUOUS = "ambiguous"
KIND_UNASSIGNED = "unassigned"

# The bucket for a row without a project, keyed on why it has none.
_BUCKET_BY_MEMBERSHIP = {
    SHARED: KIND_SHARED,
    AMBIGUOUS: KIND_AMBIGUOUS,
    UNASSIGNED: KIND_UNASSIGNED,
}

_BUCKET_LABEL = {
    KIND_SHARED: "Shared across projects",
    KIND_AMBIGUOUS: "Ambiguous ownership",
    KIND_UNASSIGNED: "Unassigned",
}

_BUCKET_WHY = {
    KIND_SHARED: ("Used by more than one project. Counted once here rather "
                  "than in each, so no project's size is inflated by the "
                  "plumbing it shares."),
    KIND_AMBIGUOUS: ("Evidence points at more than one project and none of it "
                     "settles the question."),
    KIND_UNASSIGNED: ("No tag, no name rule and no ownership link put this in "
                      "a project."),
}

@dataclass
class ProjectSummary:
    """One circle's worth of facts."""

    project_id: str
    display_name: str
    kind: str = KIND_PROJECT
    # Keys of rows present in THIS scan; deleted members of a saved group are not counted.
    resource_keys: tuple = ()
    unused: int = 0
    active: int = 0
    unknown: int = 0
    grouping_method: str = ""
    why: str = ""

    @property
    def bubble_id(self):
        return self.project_id

    @property
    def resource_count(self):
        return len(self.resource_keys)

    @property
    def unused_pct(self):
        """None, not 0.0, for an empty project: the question does not apply."""
        if not self.resource_keys:
            return None
        return 100.0 * self.unused / len(self.resource_keys)

    @property
    def all_unknown(self):
        """Every resource has unreadable telemetry - not "confidently clean"."""
        return bool(self.resource_keys) and self.unknown == len(self.resource_keys)

    def as_dict(self):
        return {
            "project_id": self.project_id,
            "display_name": self.display_name,
            "kind": self.kind,
            "resource_keys": list(self.resource_keys),
            "resource_count": self.resource_count,
            "unused": self.unused,
            "active": self.active,
            "unknown": self.unknown,
            "unused_pct": self.unused_pct,
            "all_unknown": self.all_unknown,
            "grouping_method": self.grouping_method,
            "why": self.why,
        }


@dataclass
class ProjectOverview:
    """Every summary, plus what a reader needs to interpret them."""

    projects: tuple = ()
    notes: tuple = ()

    @property
    def has_named_projects(self):
        """True once anything is a project because a person said so."""
        return any(p.kind == KIND_PROJECT and p.resource_keys
                   and p.grouping_method in ("tag", "rule", "named")
                   for p in self.projects)


def _identity(row):
    """(id, display name, kind) of this row's summary: its project, else the bucket its
    membership names (unassigned if blank)."""
    project_id = (row.get("project_id") or "").strip()
    if project_id:
        # Keyed on the id; the label may be empty or duplicated.
        return project_id, (row.get("project_group") or project_id), KIND_PROJECT
    kind = _BUCKET_BY_MEMBERSHIP.get(row.get("membership"), KIND_UNASSIGNED)
    return kind, _BUCKET_LABEL[kind], kind


def count_usage(summary, row):
    """Add one row's usage state to a summary's unused/active/unknown."""
    usage = row.get("usage_state") or USAGE_UNKNOWN
    if usage == USAGE_UNUSED:
        summary.unused += 1
    elif usage == USAGE_ACTIVE:
        summary.active += 1
    else:
        summary.unknown += 1


def summarize_projects(rows, remembered=()):
    """One entry per project and bucket; counts add up to len(rows). `remembered` adds
    saved groups with no live members (listed, never drawn)."""
    order = []
    by_id = {}
    counts = {}

    for row in rows:
        project_id, display_name, kind = _identity(row)
        summary = by_id.get(project_id)
        if summary is None:
            summary = ProjectSummary(
                project_id=project_id, display_name=display_name, kind=kind,
                why=_BUCKET_WHY.get(kind, ""))
            by_id[project_id] = summary
            order.append(project_id)
            counts[project_id] = []
        elif kind == KIND_PROJECT and not summary.display_name:
            summary.display_name = display_name

        counts[project_id].append(member_key(row))
        count_usage(summary, row)
        if kind == KIND_PROJECT and not summary.grouping_method:
            summary.grouping_method = row.get("grouping_method") or ""

    # A saved group whose members are all gone, unless a live row claimed its id.
    for gid, name in remembered:
        if gid in by_id:
            continue
        by_id[gid] = ProjectSummary(
            project_id=gid, display_name=name, kind=KIND_PROJECT,
            grouping_method="named",
            why="Saved group with no resources left in this scan.")
        order.append(gid)
        counts[gid] = []

    for project_id in order:
        by_id[project_id].resource_keys = tuple(counts[project_id])

    # Projects first, largest first; buckets always after them.
    def sort_key(summary):
        return (summary.kind != KIND_PROJECT,
                -summary.resource_count,
                summary.display_name.lower())

    summaries = tuple(sorted((by_id[i] for i in order), key=sort_key))
    return ProjectOverview(projects=summaries, notes=_notes(summaries))


def _notes(summaries):
    """Caveats a reader needs before comparing circles, each only when it applies."""
    notes = []
    unknown_only = [s for s in summaries if s.all_unknown]
    if unknown_only:
        notes.append(
            f"{len(unknown_only)} project(s) have no readable usage telemetry "
            f"at all. They show 0 potentially unused because nothing could be "
            f"measured, not because nothing is idle.")
    empty = [s for s in summaries if not s.resource_keys]
    if empty:
        notes.append(
            f"{len(empty)} saved group(s) have no resources in this scan. They "
            f"are listed with no circle - an empty project has no size and no "
            f"unused percentage.")
    return tuple(notes)
