"""The project list: one entry per project, built by naming/project_list.py."""

from fastapi import APIRouter

from aws_resource_audit.naming.project_list import project_list

from .. import stores
from ..schemas import Project
from ..store import SnapshotMissing

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=list[Project], summary="Every project, with its name and source")
def list_projects():
    """Listed without a scan too: saved names outlive scans, as in GET /api/groups."""
    store = stores.snapshot_store()
    try:
        rows = store.load().rows
    except SnapshotMissing:
        rows = []
    return project_list(rows, store.groups_path)
