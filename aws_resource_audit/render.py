"""The rendering half: a snapshot in, report files out, with no AWS access at all.
run() and aws_regenerate_report.py both go through render_outputs(), so they cannot drift."""

import os

from .analyze.billing_coverage import assess_billing_coverage
from .analyze.cost import attribute_costs
from .analyze.group_audit import audit_groups
from .analyze.architecture import architecture_graph, project_architecture
from .analyze.graph import contract_graph
from .console import say, set_quiet
from .analyze.project_summary import KIND_PROJECT, summarize_projects
from .analyze.service_summary import summarize_services
from .naming.effective import remembered_empty_groups, resolve_effective_membership
from .present.csv import CSV_FIELDNAMES, write_csv
from .present.graph.drawio import write_drawio
from .present.graph.mermaid import write_mermaid
from .present.bubbles import overview_payload
from .present.html import generate_html_report
from .present.json import write_json
from .present.markdown import write_grouping_audit
from .present.graph.mermaid import render_mermaid
from .settings import (load_settings, output_paths, project_drawio_filename,
                       project_mermaid_filename)
from .snapshot import read_snapshot


# CSV_FIELDNAMES is re-exported from present/csv.py, where the writer lives -
# aws_resource_audit/__init__.py and tests/test_json.py both reach it by this name.


def _say_graph_stats(stats):
    """The graph summary line, printed on this side because contraction is."""
    say(f"Graph: {stats['nodes']} node(s), {stats['edges']} edge(s) - "
        f"{stats['grouping_edges']} grouping, {stats['report_only_edges']} report-only, "
        f"{stats['attribute_edges']} shared-attribute, {stats['dangling_edges']} dangling"
        + (f" ({stats['merged']} duplicate detection(s) merged)" if stats["merged"] else ""))
    if stats.get("hidden_nodes"):
        say(f"       {stats['hidden_nodes']} supporting resource(s) contracted away, "
            f"{stats['bridged_edges']} link(s) redrawn through them")


def render_outputs(snapshot, paths, settings):
    """Write every rendered output for one snapshot, always all together, so no
    file is left stale from an older run."""
    rows = snapshot.rows

    # Re-apply the saved group store, so a rename is a re-render, not a re-scan.
    # Quiet: run() already printed the narration. Applying it twice is a no-op.
    previous = set_quiet(True)
    try:
        resolve_effective_membership(rows, paths.get("groups"))
        remembered = remembered_empty_groups(rows, paths.get("groups"))
    finally:
        set_quiet(previous)

    # Re-derived, not stored: a pure function of rows, billing and coverage, so
    # wording and thresholds are never frozen into a scan.
    cost_report = attribute_costs(rows, snapshot.billing,
                                  coverage_entries=snapshot.coverage)
    billing_coverage = assess_billing_coverage(
        snapshot.billing, coverage_entries=snapshot.coverage, rows=rows)
    # Contracted here so "graph_all_nodes" is a re-render. The Graph tab may show every
    # resource; the Diagram tab and .mmd draw the architecture graph instead.
    overview = summarize_projects(rows, remembered=remembered)

    graph = snapshot.graph
    if graph and not settings.graph_all_nodes:
        graph = contract_graph(graph)
    diagram_graph = architecture_graph(snapshot.graph or {}, rows)
    if graph.get("stats"):
        _say_graph_stats(graph["stats"])

    # One .mmd per project, from the same functions the API uses. Projects only:
    # a diagram of what nobody owns is not an architecture.
    project_diagrams = []
    if diagram_graph["nodes"]:
        for summary in overview.projects:
            if summary.kind != KIND_PROJECT or not summary.resource_keys:
                continue
            extracted = project_architecture(diagram_graph, snapshot.graph or {}, rows,
                                             summary.project_id)
            if not extracted["nodes"]:
                continue
            source = render_mermaid(extracted, rows)
            filename = project_mermaid_filename(summary.project_id)
            with open(os.path.join(os.path.dirname(paths["mermaid"]), filename), "w") as f:
                f.write(source)
            drawio_file = project_drawio_filename(summary.project_id)
            write_drawio(extracted, rows, os.path.join(os.path.dirname(paths["drawio"]), drawio_file),
                         title=summary.display_name)
            project_diagrams.append(
                (summary.project_id, summary.display_name, source, filename, drawio_file))

    write_csv(rows, paths["csv"])

    write_json(rows, paths["json"])

    generate_html_report(rows, paths["html"], graph=graph if settings.graph_in_html else None,
                         diagram_graph=diagram_graph if settings.graph_in_html else None,
                         generated_at=snapshot.scanned_at,
                         coverage=snapshot.coverage,
                         billing_coverage=billing_coverage,
                         cost_report=cost_report,
                         overview=(overview_payload(
                             overview, summarize_services(rows, snapshot.billing),
                             default_metric=settings.bubbles_default_metric,
                             scanned_at=snapshot.scanned_at,
                             account=snapshot.account)
                             if settings.bubbles_in_html else None),
                         project_diagrams=project_diagrams)

    write_mermaid(diagram_graph, rows, paths["mermaid"],
                  title=f"AWS dependencies - account {snapshot.account}")
    write_drawio(diagram_graph, rows, paths["drawio"],
                 title=f"AWS architecture - account {snapshot.account}")

    write_grouping_audit(audit_groups(rows, notes=snapshot.grouping),
                         output_path=paths["audit"],
                         coverage=snapshot.coverage,
                         billing_coverage=billing_coverage,
                         cost_report=cost_report)


def render(args, *, settings=None):
    """Render the report from an existing snapshot. No AWS, no cost."""
    settings = settings if settings is not None else load_settings()
    paths = output_paths(settings)
    snapshot = read_snapshot(paths["snapshot"])

    say(f"Rendering {len(snapshot.rows)} resource(s) from {os.path.abspath(paths['snapshot'])}")
    if snapshot.scanned_at:
        say(f"Scanned {snapshot.scanned_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            + (f" - account {snapshot.account}" if snapshot.account else ""))

    render_outputs(snapshot, paths, settings)
