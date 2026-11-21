"""Starting, cancelling and polling a scan. Cancel stops before the next AWS step and
saves nothing. No "scan and wait": a 202 plus polling is the interface."""

import os

from fastapi import APIRouter, status

from aws_resource_audit.region_store import load_active_regions
from aws_resource_audit.settings import OUTPUT_FILENAMES

from .. import deps, stores
from ..schemas import DefaultRegions, ScanRequest, ScanStatus
from ..scan_runner import runner
from ..store import SnapshotMissing, SnapshotStore

router = APIRouter(prefix="/api/scans", tags=["scans"])


def _with_log_url(current):
    """The runner's status plus where the full scan log is served, if one exists.
    Read from results/, the directory /files serves, so polling never reads the config."""
    name = OUTPUT_FILENAMES["scan_log"]
    exists = os.path.exists(os.path.join(deps.results_dir(), name))
    current["log_url"] = f"/files/{name}" if exists else None
    return current


@router.post(
    "",
    response_model=ScanStatus,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a scan",
    responses={409: {"description": "A scan is already running"}},
)
def start_scan(request: ScanRequest):
    """Begin a scan and return 202 at once. Settings are resolved here, so a bad
    audit_config.json is a 400 before anything starts."""
    settings = deps.get_settings()
    paths = deps.get_paths(settings)
    store = SnapshotStore(paths["snapshot"])
    return _with_log_url(
        runner.start(request.to_options(), settings, on_finish=store.invalidate))


@router.post(
    "/cancel",
    response_model=ScanStatus,
    summary="Ask the running scan to stop",
)
def cancel_scan():
    """Ask the running scan to stop; returns the current status (200 even if none runs)."""
    return _with_log_url(runner.cancel())


@router.get(
    "/status",
    response_model=ScanStatus,
    summary="Progress of the current or most recent scan",
)
def scan_status():
    """Polled about every 1.5s. Server-side state, so a reloaded page rejoins the scan;
    "done"/"failed" persist after it ends."""
    return _with_log_url(runner.status())


@router.get(
    "/default-regions",
    response_model=DefaultRegions,
    summary="The saved region list for the last scan's account",
)
def default_regions():
    """The saved region list for the last scan's account; empty with no scan. No AWS call."""
    paths = deps.get_paths()
    try:
        account = stores.snapshot_store(paths).load().account or ""
    except SnapshotMissing:
        return DefaultRegions()
    saved = load_active_regions(paths["active_regions"], account) or {}
    return DefaultRegions(account=account, **saved)
