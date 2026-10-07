"""Run configuration: audit_config.json (detection mechanisms, output location and
other standing choices) and the output layout. Unknown keys are errors, never skipped."""

import json
import os
from dataclasses import dataclass, field

from .config import BUBBLE_METRIC_IDS
from .errors import AuditError
from .connection_types.vocabulary import CONN_OFF, CONN_ON, CONN_REPORT_ONLY

# Read from the current directory, never the output directory: it is an input you
# maintain, and cd-ing to another config renders that scan instead.
CONFIG_FILENAME = "audit_config.json"

# Overrides Settings.role_name, e.g. in a container. Only used when assume_role is true.
ROLE_NAME_ENV = "AWS_AUDIT_ROLE_NAME"

VALID_STATES = (CONN_ON, CONN_REPORT_ONLY, CONN_OFF)

# Fixed names inside the output directory, since the outputs link to each other by
# name. The snapshot is the exception: see Settings.snapshot_file.
OUTPUT_FILENAMES = {
    "csv": "aws_inventory.csv",
    "json": "aws_inventory.json",
    "html": "aws_inventory.html",
    "mermaid": "aws_resource_graph.mmd",
    "drawio": "aws_architecture.drawio",
    "audit": "grouping_audit.md",
    "groups": "aws_project_groups.json",
    "active_regions": "aws_active_regions.json",
}

def project_mermaid_filename(project_id):
    """The .mmd for one project's diagram. Shared by the writer and the report's link;
    sanitised and truncated because a project id can be any tag value."""
    return _project_filename(project_id, "mermaid")


def project_drawio_filename(project_id):
    """The .drawio a single project's diagram is written to; same rule as the .mmd."""
    return _project_filename(project_id, "drawio")


def _project_filename(project_id, output):
    stem, ext = OUTPUT_FILENAMES[output].rsplit(".", 1)
    safe = "".join(c if (c.isalnum() or c in "-_") else "-"
                   for c in str(project_id)).strip("-")[:60]
    return f"{stem}-{safe or 'project'}.{ext}"


# Debug dumps, never read back: what AWS returned per resource, and the rows as
# collection finished with them.
RUNTIME_FILENAMES = {
    "raw_capture": "aws_raw_capture.jsonl",
    "normalized_scan_results": "aws_normalized_scan_results.json",
}

# Finished outputs and the scan's working notes are kept in separate subdirectories.
RESULTS_DIRNAME = "results"
RUNTIME_DIRNAME = "runtime"


@dataclass
class Settings:
    """Everything audit_config.json can say. Defaults here ARE the no-config
    behaviour, so the file only ever needs to carry what you changed."""

    # {connection-type id or fnmatch glob: "on" | "report-only" | "off"},
    # applied in file order after authoritative_only.
    connection_types: dict = field(default_factory=dict)
    # Turn off every mechanism not read straight from an AWS API: fewer, smaller,
    # more trustworthy project groups.
    authoritative_only: bool = False
    # Draw the dependency-graph panel in the HTML report. Turn off if the CDN
    # the graph library loads from is blocked.
    graph_in_html: bool = True
    # Draw every resource in the Graph tab instead of contracting supporting ones.
    # The Diagram tab is always the contracted architecture view.
    graph_all_nodes: bool = False
    # Draw the overview (bubble chart) in the HTML report; needs no CDN.
    bubbles_in_html: bool = True
    # The overview's opening metric; validated at load. "cost" exists only By service.
    bubbles_default_metric: str = "resource_count"
    # Where every output goes (results/ and runtime/), relative to this config's
    # directory. One named directory, so one ignore line covers it.
    output_dir: str = "./Output"
    # The scan snapshot's filename inside output_dir, never a path.
    snapshot_file: str = "aws_scan.json"
    # Assume an IAM role after authenticating with the profile. Off by default.
    assume_role: bool = False
    # Role name in the scanned account (not an ARN), so one setting works for every
    # account that deployed iam/aws-audit-role.yaml. ROLE_NAME_ENV overrides it.
    role_name: str = "AWS-audit-role"


_BOOL_KEYS = ("authoritative_only", "graph_in_html", "graph_all_nodes",
              "bubbles_in_html", "assume_role")
_STR_KEYS = ("output_dir", "snapshot_file", "role_name", "bubbles_default_metric")


def _validate_snapshot_file(path, name):
    """The snapshot name must be a bare .json filename: outputs link to each other
    within one directory, and .gitignore matches the account map by name."""
    if os.path.basename(name) != name or os.path.isabs(name):
        raise AuditError(
            f"{path}: 'snapshot_file' must be a bare filename inside output_dir, "
            f"got {name!r}. Use 'output_dir' to choose the directory.")
    if not name.endswith(".json"):
        raise AuditError(
            f"{path}: 'snapshot_file' must end in .json, got {name!r} - the snapshot "
            "is a full inventory of the account and .gitignore matches it by name.")


def _validate_default_metric(path, metric):
    """Refused rather than silently defaulted, so a typo is not mistaken for an ignored setting."""
    if metric not in BUBBLE_METRIC_IDS:
        raise AuditError(
            f"{path}: 'bubbles_default_metric' must be one of "
            f"{', '.join(BUBBLE_METRIC_IDS)}, got {metric!r}")


def load_settings(path=CONFIG_FILENAME):
    """Read audit_config.json. Absent = defaults; present but unusable = AuditError,
    never a silently different configuration."""
    if not path or not os.path.exists(path):
        return Settings()
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise AuditError(f"{path}: could not be read ({e})")
    if not isinstance(data, dict):
        raise AuditError(f"{path}: expected a JSON object at the top level")

    unknown = sorted(set(data) - set(Settings().__dict__))
    if unknown:
        known = ", ".join(sorted(Settings().__dict__))
        raise AuditError(f"{path}: unknown setting(s) {', '.join(unknown)}. Known settings: {known}")

    settings = Settings()
    for key in _BOOL_KEYS:
        if key in data:
            if not isinstance(data[key], bool):
                raise AuditError(f"{path}: '{key}' must be true or false, got {data[key]!r}")
            setattr(settings, key, data[key])

    for key in _STR_KEYS:
        if key in data:
            # Checked before the emptiness test so `true` reports its type
            # rather than being described as an empty string.
            if not isinstance(data[key], str):
                raise AuditError(f"{path}: '{key}' must be a string, got {data[key]!r}")
            if not data[key]:
                raise AuditError(f"{path}: '{key}' must not be empty")
            setattr(settings, key, data[key])
    _validate_snapshot_file(path, settings.snapshot_file)
    _validate_default_metric(path, settings.bubbles_default_metric)

    types = data.get("connection_types", {})
    if not isinstance(types, dict):
        raise AuditError(f"{path}: 'connection_types' must be an object of "
                         "{{\"connection-type-id\": \"on|report-only|off\"}}")
    for cid, state in types.items():
        if state not in VALID_STATES:
            raise AuditError(f"{path}: connection type '{cid}' has state {state!r}; "
                             f"expected one of {', '.join(VALID_STATES)}")
    settings.connection_types = dict(types)
    return settings


def resolved_role_name(settings):
    """The role to assume once assume_role is on; ROLE_NAME_ENV wins over the file."""
    return os.environ.get(ROLE_NAME_ENV) or settings.role_name


def output_paths(settings):
    """Path per output file, creating results/ and runtime/ under output_dir. Every file
    is written on every run; the dict stays flat (paths["csv"]), snapshot included."""
    output_dir = settings.output_dir or Settings.output_dir
    results_dir = os.path.join(output_dir, RESULTS_DIRNAME)
    runtime_dir = os.path.join(output_dir, RUNTIME_DIRNAME)
    os.makedirs(results_dir, exist_ok=True)
    os.makedirs(runtime_dir, exist_ok=True)
    paths = {kind: os.path.join(results_dir, name)
             for kind, name in OUTPUT_FILENAMES.items()}
    paths["snapshot"] = os.path.join(results_dir, settings.snapshot_file)
    for kind, name in RUNTIME_FILENAMES.items():
        paths[kind] = os.path.join(runtime_dir, name)
    return paths
