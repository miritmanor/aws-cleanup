"""Where this server's data lives: AUDIT_DATA_DIR, not the cwd. It always overrides
output_dir, since in a container the mounted volume decides where files go."""

import os

from aws_resource_audit.settings import (
    CONFIG_FILENAME,
    RESULTS_DIRNAME,
    Settings,
    load_settings,
    output_paths,
)

# Overridable so tests can point a whole app at a temp directory without
# touching the environment of the process running them.
_DATA_DIR_ENV = "AUDIT_DATA_DIR"

# Uploaded documents live on the data volume beside the snapshot, surviving restarts.
DOCUMENTS_DIRNAME = "documents"


def data_dir():
    """The directory holding the scan, config and group names; "." outside a container."""
    return os.environ.get(_DATA_DIR_ENV) or "."


def documents_dir():
    """Where uploaded documents are stored; created on first write."""
    return os.path.join(data_dir(), DOCUMENTS_DIRNAME)


def results_dir():
    """The results/ subdirectory, mounted at /files. runtime/ (raw debug dumps) is
    deliberately outside it, so never served over HTTP."""
    return os.path.join(data_dir(), RESULTS_DIRNAME)


def config_path():
    """audit_config.json, inside the data directory, so one mount carries everything."""
    return os.path.join(data_dir(), CONFIG_FILENAME)


def agent_config_path():
    """agent_config.json, beside audit_config.json; a separate file for a separate component."""
    from .agent.settings import AGENT_CONFIG_FILENAME
    return os.path.join(data_dir(), AGENT_CONFIG_FILENAME)


def get_settings():
    """The active Settings, read fresh on every call, with output_dir forced. Raises
    AuditError if unusable (a 400)."""
    settings = load_settings(config_path())
    settings.output_dir = data_dir()
    return settings


def get_paths(settings=None):
    """Path per output file, and the snapshot. Creates the directory."""
    return output_paths(settings if settings is not None else get_settings())


def default_settings():
    """Defaults with output_dir forced, for writing a config for the first time."""
    settings = Settings()
    settings.output_dir = data_dir()
    return settings
