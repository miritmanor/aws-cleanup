"""grouping_audit.md: the grouping findings from analyze/group_audit.py, worded as
explanations a person can act on."""

from ..console import say
from ..coverage import NEXT_STEP, WORST_FIRST
from .cost import render_billing_coverage, render_unallocated


def render_coverage(entries):
    """What the scan could not see, at the top: it changes how everything below reads."""
    if not entries:
        return ""
    gaps = [e for e in entries
            if e.get("status") not in ("complete",)]
    lines = ["## Scan coverage\n\n"]
    if not gaps:
        lines.append(f"All {len(entries)} lookup(s) completed. Nothing was "
                     "denied, skipped or cut short.\n\n")
        return "".join(lines)
    lines.append(f"{len(gaps)} of {len(entries)} lookup(s) did not complete. "
                 "Where a resource type appears here, this scan cannot tell "
                 "\"none exist\" from \"none were visible\", and nothing below "
                 "should be read as proof that something is unused.\n\n")
    lines.append("| Scope | Resource type | What | Status | Why |\n")
    lines.append("|---|---|---|---|---|\n")
    for e in sorted(gaps, key=lambda e: (e.get("scope", ""), e.get("service", ""))):
        why = (e.get("error") or "").split("\n")[0][:80]
        lines.append(f"| {e.get('scope', '')} | {e.get('service') or '-'} | "
                     f"{e.get('capability', '')} | {e.get('status', '')} | {why} |\n")
    lines.append("\n")
    for status in WORST_FIRST:
        if status in NEXT_STEP and any(e.get("status") == status for e in gaps):
            lines.append(f"- **{status}**: {NEXT_STEP[status]}\n")
    lines.append("\n")
    return "".join(lines)


def render_grouping_audit(findings, coverage=None, billing_coverage=None,
                          cost_report=None):
    """The audit file, top to bottom: coverage, then billing, then grouping findings."""
    lines = [f"# Grouping audit\n\n{findings.total} resources scanned, "
             f"{findings.cluster_count} project(s).\n\n"]
    lines.append(render_coverage(coverage or []))
    if billing_coverage is not None:
        lines.append(render_billing_coverage(
            billing_coverage,
            billing=cost_report.billing if cost_report is not None else None))
    if cost_report is not None:
        lines.append(render_unallocated(cost_report))

    if findings.large_clusters:
        lines.append("## Oversized projects - review these first\n\n")
        lines.append("A single project holding a large share of everything scanned is the classic sign a "
                     "weak shared attribute (a default VPC, a common tag applied too broadly, etc.) bridged "
                     "two unrelated projects together. Check the why_grouped column for members that look "
                     "out of place.\n\n")
        for name, members, fraction in findings.large_clusters:
            lines.append(f"- **{name}**: {len(members)} resources ({fraction:.0%} of everything scanned)\n")
        lines.append("\n")

    if findings.weak_only_clusters:
        lines.append("## Weak-evidence-only projects\n\n")
        lines.append("Every reason recorded for these projects is a low-confidence heuristic (an env-var "
                     "text match, or an Amplify/API name-match guess) - no tag, no resolved authoritative "
                     "edge, no shared non-default VPC or IAM role. Worth a manual look before trusting the "
                     "grouping.\n\n")
        for name, members in findings.weak_only_clusters:
            sample = ", ".join(f"{m['service']}:{m['resource_id']}" for m in members[:6])
            more = "..." if len(members) > 6 else ""
            lines.append(f"- **{name}**: {len(members)} resources - {sample}{more}\n")
        lines.append("\n")

    if findings.collisions:
        lines.append(f"## Coincidental id/name collisions caught and excluded ({len(findings.collisions)})\n\n")
        lines.append("resolve_edges found a same-id/name match of the WRONG resource type and refused to "
                     "link it - see each row's connections column for exactly what it matched instead.\n\n")
        for row in findings.collisions[:25]:
            lines.append(f"- {row['service']}:{row['resource_id']}\n")
        lines.append("\n")

    if findings.dangling:
        lines.append(f"## Dangling references ({len(findings.dangling)})\n\n")
        lines.append("These resources reference something not found in this scan (deleted, or outside the "
                     "scanned regions/account) - see connections for what's missing.\n\n")
        for row in findings.dangling[:25]:
            lines.append(f"- {row['service']}:{row['resource_id']}\n")
        lines.append("\n")

    if findings.conflicts:
        lines.append(f"## Links that crossed a project boundary ({len(findings.conflicts)})\n\n")
        lines.append("Each of these is a real link between resources that belong to different "
                     "projects. The link is kept and shown; what was refused is the conclusion "
                     "that the two projects are therefore one. Previously a single link like "
                     "this merged them and the group was labelled by joining both names.\n\n")
        for note in findings.conflicts[:25]:
            lines.append(f"- {note}\n")
        lines.append("\n")

    if findings.tag_conflicts:
        lines.append(f"## Resources tagged with two different projects ({len(findings.tag_conflicts)})\n\n")
        lines.append("AWS lets a resource carry both Project and App with different values. "
                     "The first was used; the resource itself is the thing to fix.\n\n")
        for note in findings.tag_conflicts[:25]:
            lines.append(f"- {note}\n")
        lines.append("\n")

    if findings.shared:
        lines.append(f"## Shared between projects ({len(findings.shared)})\n\n")
        lines.append("Used by more than one project, so assigned to none of them. This is an "
                     "answer, not a failure to decide - forcing a shared bucket into whichever "
                     "project references it most is how a dependency becomes a false claim of "
                     "ownership.\n\n")
        for note in findings.shared[:25]:
            lines.append(f"- {note}\n")
        lines.append("\n")

    if findings.candidate_words:
        lines.append(f"## Names that could group things ({len(findings.candidate_words)})\n\n")
        lines.append("Words appearing in two or more resource names where nothing else connects "
                     "those resources - so writing a rule for one would actually change the "
                     "grouping. Nothing here is applied: add a word to \"name_rules\" in the "
                     "groups file to act on it.\n\n")
        for entry in findings.candidate_words[:25]:
            shown = ", ".join(entry["resources"][:6])
            more = ", ..." if len(entry["resources"]) > 6 else ""
            lines.append(f"- **{entry['word']}** - {len(entry['resources'])} resources: {shown}{more}\n")
        lines.append("\n")

    if not any((findings.large_clusters, findings.weak_only_clusters, findings.collisions,
                findings.dangling, findings.conflicts, findings.shared)):
        lines.append("Nothing flagged - no oversized projects, no weak-evidence-only projects, no collisions, "
                     "no dangling references.\n")

    return "".join(lines)


def summarize_grouping_audit(findings):
    """The one-line console summary. Always printed, so a bad merge is visible
    without opening a file."""
    return (f"Audit: {len(findings.large_clusters)} oversized project(s), "
            f"{len(findings.weak_only_clusters)} weak-evidence-only project(s), "
            f"{len(findings.collisions)} collision(s) caught, "
            f"{len(findings.dangling)} dangling reference(s).")


def write_grouping_audit(findings, output_path=None, coverage=None,
                         billing_coverage=None, cost_report=None):
    report = render_grouping_audit(findings, coverage=coverage,
                                   billing_coverage=billing_coverage,
                                   cost_report=cost_report)
    if output_path:
        with open(output_path, "w") as handle:
            handle.write(report)
    say(summarize_grouping_audit(findings))
    return report
