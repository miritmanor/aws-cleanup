"""Read-only views of the snapshot: rows, overview, graph, architecture diagram and the
service colour table. No AWS calls."""

from fastapi import APIRouter, Query

from aws_resource_audit.analyze.billing_coverage import assess_billing_coverage
from aws_resource_audit.analyze.cost import attribute_costs
from aws_resource_audit.analyze.architecture import architecture_graph, project_architecture
from aws_resource_audit.analyze.graph import contract_graph
from aws_resource_audit.analyze.project_summary import summarize_projects
from aws_resource_audit.analyze.service_summary import summarize_services
from aws_resource_audit.naming.effective import remembered_empty_groups
from aws_resource_audit.present.bubbles import overview_payload
from aws_resource_audit.present.cost import (
    allocation_note, below_threshold_note, period_note, service_entry_lines,
)
from aws_resource_audit.present.format import display_row
from aws_resource_audit.present.graph.icons import ICON_PACK
from aws_resource_audit.present.graph.mermaid import render_mermaid
from aws_resource_audit.present.html import build_console_url
from aws_resource_audit.present.html.template import SERVICE_META

from .. import deps, stores
from ..schemas import (
    BilledService, BillingSummary, OverviewResponse, ScanSummary,
)

router = APIRouter(prefix="/api", tags=["scan"])


@router.get("/scan", response_model=ScanSummary, summary="The latest scan")
def get_scan():
    """Every row of the latest scan via display_row() - the same function the HTML report
    uses - plus console_url. Unpaginated on purpose."""
    snapshot = stores.snapshot_store().load()
    rows = [display_row(row, extra={"console_url": build_console_url(row)})
            for row in snapshot.rows]
    return ScanSummary(
        scanned_at=snapshot.scanned_at,
        account=snapshot.account,
        regions=snapshot.regions,
        row_count=len(rows),
        rows=rows,
        coverage=snapshot.coverage,
    )


@router.get("/billing", response_model=BillingSummary,
            summary="What AWS billed, and what the scan can speak for")
def get_billing():
    """The billing half of the last scan, decided in Python and served with its wording.
    No AWS call: the billing response is stored in the snapshot."""
    snapshot = stores.snapshot_store().load()
    cost_report = attribute_costs(snapshot.rows, snapshot.billing,
                                  coverage_entries=snapshot.coverage)
    report = assess_billing_coverage(snapshot.billing,
                                     coverage_entries=snapshot.coverage,
                                     rows=snapshot.rows)
    return BillingSummary(
        queried=report.queried, complete=report.complete,
        estimated=report.estimated, error=report.error,
        currency=report.currency,
        period_start=str(report.period_start) if report.period_start else None,
        period_end=str(report.period_end) if report.period_end else None,
        observed_at=report.observed_at,
        total=str(report.total),
        allocated=str(cost_report.allocated),
        unallocated=str(cost_report.unallocated),
        below_threshold=report.below_threshold_count(),
        reconciles=cost_report.reconciles(),
        period_note=period_note(report),
        allocation_note=allocation_note(cost_report),
        below_threshold_note=below_threshold_note(report),
        services=[BilledService(
            service=entry.service, amount=str(entry.amount), state=entry.state,
            collected=list(entry.collected), gaps=list(entry.gaps),
            row_count=entry.row_count,
            lines=service_entry_lines(entry, report.currency))
            for entry in report.listed_services()],
    )


@router.get("/graph", summary="The dependency graph")
def get_graph(
    all_nodes: bool = Query(
        default=None,
        description="Draw every scanned resource instead of contracting "
                    "supporting ones away. Defaults to the graph_all_nodes "
                    "setting."),
):
    """The graph payload, contracted unless the query parameter says otherwise."""
    settings = deps.get_settings()
    snapshot = stores.snapshot_store(deps.get_paths(settings)).load()
    graph = snapshot.graph or {}
    draw_all = settings.graph_all_nodes if all_nodes is None else all_nodes
    if graph and not draw_all:
        graph = contract_graph(graph)
    return graph


@router.get("/mermaid", summary="The architecture diagram, as Mermaid source")
def get_mermaid(project_id: str = Query(
        "", description="Draw only this project and what it directly touches. "
                        "Omit for the whole account.")):
    """The same Mermaid string the CLI writes to the .mmd. The architecture graph is
    built from the whole scan first, then scoped, so grants through a role survive."""
    settings = deps.get_settings()
    snapshot = stores.snapshot_store(deps.get_paths(settings)).load()
    graph = architecture_graph(snapshot.graph or {}, snapshot.rows)
    if project_id:
        # Scoped views add the Region > VPC > subnet boxes.
        graph = project_architecture(graph, snapshot.graph or {}, snapshot.rows, project_id)
    return {"mermaid": render_mermaid(graph, snapshot.rows)}


@router.get("/overview", response_model=OverviewResponse,
            summary="The overview: by service and by project, with packed circles")
def get_overview():
    """The overview payload, the same one the HTML report embeds: counts on project_id,
    circles placed in Python, plus remembered empty groups."""
    settings = deps.get_settings()
    paths = deps.get_paths(settings)
    snapshot = stores.snapshot_store(paths).load()
    projects = summarize_projects(
        snapshot.rows,
        remembered=remembered_empty_groups(snapshot.rows, paths["groups"]))
    return overview_payload(
        projects, summarize_services(snapshot.rows, snapshot.billing),
        default_metric=settings.bubbles_default_metric,
        scanned_at=snapshot.scanned_at, account=snapshot.account)


@router.get("/architecture-icons", summary="The AWS icon pack the diagram draws with")
def get_architecture_icons():
    """An Iconify icon pack (prefix "aws") to pass to mermaid.registerIconPacks."""
    return ICON_PACK


@router.get("/service-meta", summary="Service colours and categories")
def get_service_meta():
    """SERVICE_META, served rather than copied into TypeScript, so the React legend
    stays under the test that pins it to AWS's catalogue."""
    return SERVICE_META
