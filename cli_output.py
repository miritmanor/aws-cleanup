"""What the command line prints after the library finishes: where the files are
and where the explanations are. The library itself prints facts only."""

import os

from aws_resource_audit.console import say

MESSAGES_DOC = "help/cli-messages.md"

_REPORT_FILES = [
    ("html", "sortable, filterable, grouped by project"),
    ("csv", "spreadsheet"),
    ("mermaid", "the same architecture diagram, for a README or a doc"),
    ("audit", "grouping self-review"),
    ("json", "raw JSON"),
]

_SCAN_FILES = [
    ("groups", "project-group names you have assigned - edit to name a group"),
    ("snapshot", "this scan, saved - run aws_regenerate_report.py to redraw "
                 "everything above from it without touching AWS"),
]


def print_outputs(paths, settings, after_scan):
    """The file list, then the pointer to the messages doc."""
    files = _REPORT_FILES + (_SCAN_FILES if after_scan else [])
    say("\nOpen the report:")
    for kind, hint in files:
        if kind == "html" and settings.graph_in_html:
            hint += " (+ dependency graph)"
        say(f"  {os.path.abspath(paths[kind])}   {hint}")
    here = os.path.dirname(os.path.abspath(__file__))
    say(f"\nWhat the messages above mean, and what to do about them: "
        f"{os.path.join(here, MESSAGES_DOC)}")
