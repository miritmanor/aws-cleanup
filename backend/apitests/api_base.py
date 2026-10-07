"""Shared by the test_api_* modules: a fresh app per test, pointed at a temporary
data directory and loaded with the core suite's corpus snapshot."""

import json
import os
import tempfile
import unittest
from . import _REPO  # noqa: F401  (import for the sys.path fixup side effect)
from fastapi.testclient import TestClient
import aws_resource_audit as audit
from tests import corpus


def _fresh_app(data_dir):
    """An app pointed at `data_dir`, with every cached (process-global) store dropped."""
    os.environ["AUDIT_DATA_DIR"] = data_dir
    from app import stores
    from app.main import app, mount_files
    stores.reset()
    # StaticFiles binds its directory at mount time: replace the old /files mount.
    app.router.routes = [r for r in app.router.routes
                         if getattr(r, "name", None) != "files"]
    mount_files(app)
    return TestClient(app, raise_server_exceptions=False)


class ApiTestCase(unittest.TestCase):
    """A temp data directory, and helpers to put a scan or a config in it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self._previous = os.environ.get("AUDIT_DATA_DIR")
        self.addCleanup(self._restore_env)
        self.client = _fresh_app(self.data_dir)

    def _restore_env(self):
        if self._previous is None:
            os.environ.pop("AUDIT_DATA_DIR", None)
        else:
            os.environ["AUDIT_DATA_DIR"] = self._previous

    def write_snapshot(self):
        """The golden corpus written as a real snapshot where output_paths() puts it."""
        rows, _contracted, full, notes = corpus.build()
        paths = audit.output_paths(audit.Settings(output_dir=self.data_dir))
        audit.write_snapshot(
            paths["snapshot"], rows, full, notes,
            account="123456789012", regions=["us-east-1"], scanned_at=audit.now(),
            coverage=corpus.coverage_entries(), billing=corpus.billing())
        return rows

    def write_config(self, data):
        with open(os.path.join(self.data_dir, "audit_config.json"), "w") as f:
            json.dump(data, f)

    def read_config(self):
        path = os.path.join(self.data_dir, "audit_config.json")
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return json.load(f)
