"""Downloads: the inventory CSV and the architecture Mermaid. Rendered from the snapshot
with the CLI's own functions, so they include names assigned since the scan."""

from fastapi import APIRouter, Query
from fastapi.responses import Response

from aws_resource_audit.analyze.architecture import architecture_graph, project_architecture
from aws_resource_audit.present.csv import render_csv
from aws_resource_audit.present.graph.drawio import render_drawio
from aws_resource_audit.present.graph.mermaid import render_mermaid
from aws_resource_audit.settings import OUTPUT_FILENAMES

from .. import deps, stores

router = APIRouter(prefix="/api/export", tags=["export"])


def _attachment(body, filename, media_type):
    return Response(content=body, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _snapshot():
    settings = deps.get_settings()
    return stores.snapshot_store(deps.get_paths(settings)).load()


@router.get("/inventory.csv", summary="The inventory as a CSV download")
def export_csv():
    """The columns and their order are present/csv.py's CSV_FIELDNAMES."""
    snapshot = _snapshot()
    return _attachment(render_csv(snapshot.rows), OUTPUT_FILENAMES["csv"], "text/csv")


@router.get("/architecture.mmd", summary="The architecture diagram as a Mermaid download")
def export_mermaid(project_id: str = Query(
        "", description="Download only this project's diagram. Omit for the "
                        "whole account.")):
    """The Mermaid source behind the Architecture tab. A project download is named
    after the project id (not its name, which may contain a slash)."""
    snapshot = _snapshot()
    graph = architecture_graph(snapshot.graph or {}, snapshot.rows)
    name = OUTPUT_FILENAMES["mermaid"]
    if project_id:
        graph = project_architecture(graph, snapshot.graph or {}, snapshot.rows, project_id)
        stem = name.rsplit(".", 1)[0]
        name = f"{stem}-{_safe_stem(project_id)}.mmd"
    return _attachment(render_mermaid(graph, snapshot.rows),
                       name, "text/vnd.mermaid")


@router.get("/architecture.drawio", summary="The architecture diagram as an editable draw.io file")
def export_drawio(project_id: str = Query(
        "", description="Download only this project's diagram. Omit for the "
                        "whole account.")):
    """The same architecture graph as architecture.mmd, with draw.io's AWS shapes."""
    snapshot = _snapshot()
    graph = architecture_graph(snapshot.graph or {}, snapshot.rows)
    name = OUTPUT_FILENAMES["drawio"]
    if project_id:
        graph = project_architecture(graph, snapshot.graph or {}, snapshot.rows, project_id)
        stem = name.rsplit(".", 1)[0]
        name = f"{stem}-{_safe_stem(project_id)}.drawio"
    return _attachment(render_drawio(graph, snapshot.rows), name, "application/vnd.jgraph.mxfile")


def _safe_stem(value):
    """A project id reduced to what is safe in a Content-Disposition filename."""
    keep = [c if (c.isalnum() or c in "-_") else "-" for c in value]
    return "".join(keep).strip("-")[:60] or "project"
