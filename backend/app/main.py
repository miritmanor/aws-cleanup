"""The FastAPI application: a local, single-user tool with no authentication, bound to
127.0.0.1 because it serves a full map of an AWS account. AuditError messages pass through verbatim."""

import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from aws_resource_audit import logsetup
from aws_resource_audit.errors import AuditError

from . import deps
from .api import (
    agent, config, documents, export, groups, profiles, projects, resources, scans,
)
from .scan_runner import ScanInProgress
from .store import SnapshotMissing

# At module scope so import-time failures are logged; uvicorn's own dictConfig is
# already installed and must not be disabled.
logsetup.configure(
    config_path=os.path.join(deps.data_dir(), logsetup.CONFIG_FILENAME))

logger = logging.getLogger(__name__)

app = FastAPI(
    title="AWS resource audit",
    version="0.1.0",
    summary="Inventory, staleness and dependencies for one AWS account",
    description=__doc__,
)


@app.exception_handler(SnapshotMissing)
def _snapshot_missing(request: Request, exc: SnapshotMissing):
    """404: no scan yet. Registered before AuditError, its parent class."""
    logger.info("%s %s: no snapshot (%s)", request.method, request.url.path, exc)
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(AuditError)
def _audit_error(request: Request, exc: AuditError):
    """400: the configuration or environment needs fixing (the CLI exits with the same sentence)."""
    # WARNING, and with the route: this otherwise reaches only the HTTP client,
    # so the server's own log has no record that anything went wrong.
    logger.warning("%s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(ScanInProgress)
def _scan_in_progress(request: Request, exc: ScanInProgress):
    """409: one scan at a time."""
    logger.info("%s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=409, content={"detail": str(exc)})


app.include_router(scans.router)
app.include_router(resources.router)
app.include_router(groups.router)
app.include_router(projects.router)
app.include_router(config.router)
app.include_router(export.router)
app.include_router(profiles.router)
app.include_router(agent.router)
app.include_router(documents.router)


@app.get("/api/health", tags=["meta"], summary="Is the server up")
def health():
    """Never reads the snapshot or config, so a server with no scan yet is healthy."""
    return {"status": "ok", "data_dir": deps.data_dir()}


def mount_files(application=app):
    """Mount results/ at /files (a directory, so the report's sibling links work).
    Not the data directory: runtime/ holds raw account dumps."""
    directory = deps.results_dir()
    os.makedirs(directory, exist_ok=True)
    application.mount("/files", StaticFiles(directory=directory), name="files")


mount_files()
