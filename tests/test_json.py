"""The JSON writer selects exactly the declared schema, so no internal field can leak -
for every field, including ones added later."""

import json
import unittest

from . import corpus
from .fakes import make_row

import aws_resource_audit as audit
from aws_resource_audit.present.json import render_json, serialize_rows


class PublicSchemaTests(unittest.TestCase):
    def test_only_the_declared_fields_are_emitted(self):
        rows, _, _, _ = corpus.build()
        for row in serialize_rows(rows):
            self.assertEqual(tuple(row), audit.ROW_FIELDS)

    def test_an_internal_field_cannot_leak_however_it_arrives(self):
        """The point of selecting over stripping: this row carries junk no pass
        would ever have removed, and none of it reaches the output."""
        row = make_row("EC2Instance", "i-0abc", "web")
        row["_edges"] = [{"secret": "internal"}]
        row["_invented_yesterday"] = "should not appear"
        row["_cost_hint"] = "internal"
        emitted = serialize_rows([row])[0]
        self.assertEqual(tuple(emitted), audit.ROW_FIELDS)
        self.assertNotIn("internal", json.dumps(emitted))
        self.assertNotIn("should not appear", json.dumps(emitted))

    def test_the_pipeline_no_longer_needs_to_clean_up_after_itself(self):
        """Rows keep their internal fields through the pipeline; nothing strips them."""
        rows, _, _, _ = corpus.build()
        self.assertTrue(any("_edges" in r for r in rows))
        self.assertTrue(any(r.get("_references") for r in rows))

    def test_render_json_is_valid_and_round_trips(self):
        rows, _, _, _ = corpus.build()
        parsed = json.loads(render_json(rows))
        self.assertEqual(len(parsed), len(rows))
        self.assertEqual(parsed[0]["resource_id"], rows[0]["resource_id"])

    def test_the_json_schema_matches_the_csv_columns(self):
        """JSON and CSV carry the same fields (order may differ)."""
        self.assertEqual(set(audit.ROW_FIELDS), set(audit.CSV_FIELDNAMES))


if __name__ == "__main__":
    unittest.main()
