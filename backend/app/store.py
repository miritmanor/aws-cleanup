"""The three persisted things (snapshot, group names, config), one store each. Files on
a mounted volume; the snapshot is cached on (mtime, size)."""

import json
import logging
import os
import threading

from aws_resource_audit import console
from aws_resource_audit.analyze.cost import attribute_costs
from aws_resource_audit.errors import AuditError
from aws_resource_audit.naming.effective import resolve_effective_membership
from aws_resource_audit.naming.group_names import (
    load_group_store,
    save_group_store,
)
from aws_resource_audit.present.format import display_row
from aws_resource_audit.settings import load_settings
from aws_resource_audit.snapshot import read_snapshot

from .masking import mask_row


logger = logging.getLogger(__name__)


class SnapshotMissing(AuditError):
    """No scan to read: the one AuditError that is a 404 rather than a 400."""


class SnapshotStore:
    """The scan. Reading applies the group store via resolve_effective_membership (the
    same passes a CLI re-render uses), so naming needs no re-scan."""

    def __init__(self, path, groups_path=None):
        self.path = path
        self.groups_path = groups_path
        self._lock = threading.Lock()
        self._stamp = None
        self._cached = None
        self._masked_stamp = None
        self._masked_cached = None

    def exists(self):
        return os.path.exists(self.path)

    def stamp(self):
        """(mtime, size), or None if there is no file. The cache key."""
        try:
            info = os.stat(self.path)
        except OSError as e:
            # DEBUG: "no snapshot yet" is the normal first run.
            logger.debug("cannot stat %s (%s)", self.path, e)
            return None
        return (info.st_mtime_ns, info.st_size)

    def load(self):
        """The current Snapshot, or SnapshotMissing. Locked, so a half-written file is
        never cached."""
        with self._lock:
            stamp = self.stamp()
            if stamp is None:
                raise SnapshotMissing(_missing_message(self.path))
            if stamp == self._stamp and self._cached is not None:
                return self._cached
            snapshot = read_snapshot(self.path)
            if self.groups_path:
                # Quiet: its terminal advice is noise in a server log.
                previous = console.set_quiet(True)
                try:
                    resolve_effective_membership(snapshot.rows, self.groups_path)
                finally:
                    console.set_quiet(previous)
            # Re-derived on load, as render_outputs() does, so a row's cost
            # follows the current rules and not the ones the scan ran under.
            attribute_costs(snapshot.rows, snapshot.billing,
                            coverage_entries=snapshot.coverage)
            self._stamp, self._cached = stamp, snapshot
            return snapshot

    def get_masked_rows(self):
        """Every row via display_row() then mask_row(): the only resource data the agent
        may read. Cached on the snapshot's stamp."""
        with self._lock:
            stamp = self.stamp()
            if stamp is not None and stamp == self._masked_stamp and self._masked_cached is not None:
                return self._masked_cached
        snapshot = self.load()
        masked = [mask_row(display_row(row)) for row in snapshot.rows]
        with self._lock:
            self._masked_stamp = self.stamp()
            self._masked_cached = masked
        return masked

    def invalidate(self):
        """Drop the cache. Called after a scan, so the next read is fresh even
        if the filesystem's timestamp resolution hid the change."""
        with self._lock:
            self._stamp, self._cached = None, None
            self._masked_stamp, self._masked_cached = None, None


def _missing_message(path):
    """The same sentence read_snapshot uses, so a person sees one wording
    whether they hit this through the API or the CLI."""
    return (f"{path}: no scan snapshot here. Run a scan first - it writes the "
            "snapshot this reads from.")


class GroupStore:
    """Project-group names: written by scans and the naming endpoints, kept apart by the scan lock."""

    def __init__(self, path):
        self.path = path

    def load(self):
        """{"groups": {gid: {name, members, updated_at}}}; empty (with a warning) if
        missing or corrupt."""
        return load_group_store(self.path)

    def save(self, store):
        save_group_store(self.path, store)


class ConfigStore:
    """audit_config.json, validated by the core's own load_settings."""

    def __init__(self, path):
        self.path = path

    def exists(self):
        return os.path.exists(self.path)

    def read_raw(self):
        """The file as written, or {} if absent, so the editor can tell unset from default."""
        if not self.exists():
            return {}
        try:
            with open(self.path) as handle:
                data = json.load(handle)
        except (json.JSONDecodeError, OSError) as e:
            # The AuditError carries the message; only the traceback and the
            # original exception type are lost, so DEBUG puts them back.
            logger.debug("reading %s failed", self.path, exc_info=True)
            raise AuditError(f"{self.path}: could not be read ({e})")
        if not isinstance(data, dict):
            raise AuditError(f"{self.path}: expected a JSON object at the top level")
        return data

    def write(self, data):
        """Validate with a real load_settings() of a temp file beside the target, then
        atomically replace. Nothing is written if validation fails."""
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        temp = os.path.join(directory, f".{os.path.basename(self.path)}.candidate")
        with open(temp, "w") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        try:
            load_settings(temp)
        except AuditError as e:
            os.unlink(temp)
            # Name the user's file, not the temp file, in the message.
            raise AuditError(str(e).replace(temp, self.path, 1))
        except Exception:
            os.unlink(temp)
            raise
        os.replace(temp, self.path)
