"""The raw JSON dump: exactly rows.ROW_FIELDS, selected, so internal fields cannot leak."""

import json

from .format import display_row


def serialize_rows(all_rows):
    return [display_row(row) for row in all_rows]


def render_json(all_rows):
    return json.dumps(serialize_rows(all_rows), indent=2, default=str)


def write_json(all_rows, path):
    with open(path, "w") as handle:
        handle.write(render_json(all_rows))
