"""The process's shared store instances, keyed by path so tests can point the app elsewhere."""

import threading

from . import deps
from .store import ConfigStore, GroupStore, SnapshotStore

_lock = threading.Lock()
_snapshots = {}


def snapshot_store(paths=None):
    paths = paths if paths is not None else deps.get_paths()
    key = (paths["snapshot"], paths["groups"])
    with _lock:
        store = _snapshots.get(key)
        if store is None:
            store = SnapshotStore(paths["snapshot"], groups_path=paths["groups"])
            _snapshots[key] = store
        return store


def group_store(paths=None):
    """Stateless, so a fresh one each time is honest - it holds a path."""
    paths = paths if paths is not None else deps.get_paths()
    return GroupStore(paths["groups"])


def config_store():
    return ConfigStore(deps.config_path())


def reset():
    """Drop every cached store (tests, or a change of output_dir)."""
    with _lock:
        _snapshots.clear()
