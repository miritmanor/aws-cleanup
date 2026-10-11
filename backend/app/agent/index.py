"""The process's document index handle (keyed by directory, so tests can repoint it),
and the rebuild. Separate from agent_loop so a rebuild never needs a chat model."""

import os
import threading

from aws_resource_audit.errors import AuditError

from .. import deps
from . import install
from .documents import DocumentStore
from .rag import open_index, rebuild_index
from .settings import load_agent_settings

_lock = threading.Lock()
_rebuild_lock = threading.Lock()
_indexes: dict[str, object] = {}


def persist_dir() -> str:
    """Where the collection lives: settings.chroma_dir, which is a bare
    directory NAME, resolved against the data volume."""
    settings = load_agent_settings(deps.agent_config_path())
    return os.path.join(deps.data_dir(), settings.chroma_dir)


def get_index():
    """The shared Chroma collection, opened on first use. Opening does not
    build: a brand-new data volume has an empty index until a rebuild."""
    directory = persist_dir()
    with _lock:
        store = _indexes.get(directory)
        if store is None:
            store = open_index(directory)
            _indexes[directory] = store
        return store


def rebuild() -> int:
    """Re-embed the corpus; returns the chunk count. Serialised so two rebuilds never interleave."""
    reason = install.not_installed_reason()
    if reason:
        raise AuditError(reason)
    with _rebuild_lock:
        return rebuild_index(get_index(), DocumentStore(deps.documents_dir()))


def reset() -> None:
    """Drop the cached handle. For tests, and for a config change that moves
    chroma_dir - after which the open collection is not this server's."""
    with _lock:
        _indexes.clear()
