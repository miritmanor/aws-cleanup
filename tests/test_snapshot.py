"""The scan snapshot: its schema SELECTS rather than strips, and a missing, stale or
corrupt file produces an actionable sentence. Completeness is covered by test_golden."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from . import corpus

import aws_resource_audit as audit
from aws_resource_audit.errors import AuditError
from aws_resource_audit.snapshot import (
    SCHEMA_VERSION,
    SNAPSHOT_ROW_FIELDS,
    read_snapshot,
    snapshot_row,
    write_snapshot,
)


class SnapshotSchemaTests(unittest.TestCase):
    """What a stored row may contain."""

    @classmethod
    def setUpClass(cls):
        cls.rows, _contracted, cls.full, cls.notes = corpus.build()

    def test_the_public_schema_survives_intact(self):
        stored = snapshot_row(self.rows[0])
        for name in audit.ROW_FIELDS:
            self.assertIn(name, stored)

    def test_references_are_carried_because_three_outputs_need_them(self):
        """_references is stored: the connections column, audit and graph derive from it."""
        self.assertIn("_references", SNAPSHOT_ROW_FIELDS)
        linked = [r for r in self.rows if r["_references"]]
        self.assertTrue(linked, "corpus has no linked rows; this proves nothing")
        self.assertTrue(snapshot_row(linked[0])["_references"])

    def test_internal_fields_are_not_representable(self):
        """The writer names its fields, so a new internal field cannot leak."""
        row = dict(self.rows[0])
        row["_secret"] = "should never reach the file"
        stored = snapshot_row(row)
        leaked = sorted(k for k in stored if k.startswith("_")
                        and k not in ("_references", "_detected_project_id",
                                      "_detected_project_group", "_detected_grouping_method"))
        self.assertEqual(leaked, [], f"internal fields reached the snapshot: {leaked}")

    def test_a_stored_row_is_json_serializable(self):
        """Stored rows survive a strict JSON round trip (no default=str crutch)."""
        stored = [snapshot_row(r) for r in self.rows]
        json.loads(json.dumps(stored))


class SnapshotRoundTripTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.path = os.path.join(self.tmpdir.name, "aws_scan.json")
        rows, _contracted, full, notes = corpus.build()
        write_snapshot(self.path, rows, full, notes, account="123456789012",
                       regions=["us-east-1", "eu-west-1"],
                       scanned_at=datetime(2026, 6, 1, 12, tzinfo=timezone.utc))

    def test_the_metadata_comes_back(self):
        snapshot = read_snapshot(self.path)
        self.assertEqual(snapshot.account, "123456789012")
        self.assertEqual(snapshot.regions, ["us-east-1", "eu-west-1"])
        self.assertEqual(snapshot.scanned_at,
                         datetime(2026, 6, 1, 12, tzinfo=timezone.utc))

    def test_dates_come_back_as_datetimes(self):
        rows = read_snapshot(self.path).rows
        dated = [r for r in rows if r["created"]]
        self.assertTrue(dated)
        self.assertIsInstance(dated[0]["created"], datetime)

    def test_a_row_with_no_dates_comes_back_empty_not_broken(self):
        """Missing dates ("" or None) are stored as null, never the string "None"."""
        rows = read_snapshot(self.path).rows
        undated = [r for r in rows if not r["created"]]
        self.assertTrue(undated, "corpus has no undated rows; this proves nothing")
        self.assertIsNone(undated[0]["created"])
        self.assertEqual(audit.display_row(undated[0])["created"], "")

    def test_tags_come_back_as_a_dict(self):
        """A dict on the row, one string in the CSV cell. Storing the rendered
        form would make the tags column un-regroupable on a re-render."""
        rows = read_snapshot(self.path).rows
        tagged = [r for r in rows if r["tags"]]
        self.assertTrue(tagged, "corpus has no tagged rows; this proves nothing")
        self.assertIsInstance(tagged[0]["tags"], dict)

    def test_the_graph_is_stored_uncontracted(self):
        """The full graph is stored, so graph_all_nodes is a re-render."""
        snapshot = read_snapshot(self.path)
        contracted = audit.contract_graph(snapshot.graph)
        self.assertGreater(len(snapshot.graph["nodes"]), len(contracted["nodes"]))

    def test_grouping_notes_survive_for_the_audit(self):
        snapshot = read_snapshot(self.path)
        findings = audit.audit_groups(snapshot.rows, notes=snapshot.grouping)
        self.assertTrue(audit.render_grouping_audit(findings))

    def test_row_indices_are_not_stored(self):
        """grouping_links (row indices) is not stored."""
        with open(self.path) as handle:
            data = json.load(handle)
        self.assertNotIn("grouping_links", data["grouping"])


class AtomicWriteTests(unittest.TestCase):
    """A reader never sees a half-written snapshot: it is written atomically."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.path = os.path.join(self.tmpdir.name, "aws_scan.json")
        self.rows, _contracted, self.graph, self.notes = corpus.build()

    def _write(self):
        write_snapshot(self.path, self.rows, self.graph, self.notes,
                       account="123456789012", regions=["us-east-1"],
                       scanned_at=audit.now())

    def test_the_target_only_ever_exists_complete(self):
        """Written beside and renamed, so the name either has the old scan or
        the new one - never a prefix of the new one."""
        self._write()
        first = read_snapshot(self.path)
        self._write()
        self.assertEqual(len(read_snapshot(self.path).rows), len(first.rows))

    def test_no_temporary_file_is_left_behind(self):
        self._write()
        self.assertEqual(sorted(os.listdir(self.tmpdir.name)), ["aws_scan.json"])

    def _write_failing(self):
        """A write that dies part-way through json.dump (via a failing __str__)."""
        class Explodes:
            def __str__(self):
                raise ValueError("boom")

        return write_snapshot(
            self.path, [dict(self.rows[0], tags={"x": Explodes()})],
            self.graph, self.notes, account="123456789012",
            regions=["us-east-1"], scanned_at=audit.now())

    def test_a_failed_write_does_not_destroy_the_previous_scan(self):
        """The property opening the target in place cannot have: a scan that
        dies while serialising leaves the last good snapshot readable."""
        self._write()
        before = read_snapshot(self.path).rows

        with self.assertRaises(ValueError):
            self._write_failing()

        self.assertEqual(len(read_snapshot(self.path).rows), len(before))

    def test_a_failed_write_leaves_no_account_map_behind(self):
        """A failed write leaves no temp file behind (it would be an untracked account map)."""
        with self.assertRaises(ValueError):
            self._write_failing()
        self.assertEqual(os.listdir(self.tmpdir.name), [],
                         "a partial snapshot was left in the output directory")

    def test_the_temporary_file_is_in_the_target_directory(self):
        """The temp file is in the target's directory, so os.replace stays atomic."""
        import inspect

        from aws_resource_audit import snapshot as snapshot_module
        source = inspect.getsource(snapshot_module.write_snapshot)
        self.assertIn("os.path.dirname(path)", source)
        self.assertIn("os.replace", source)


class UnreadableSnapshotTests(unittest.TestCase):
    """An unusable snapshot gets one answer: name the file and say to run a scan."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def _write(self, content):
        path = os.path.join(self.tmpdir.name, "aws_scan.json")
        with open(path, "w") as handle:
            handle.write(content if isinstance(content, str) else json.dumps(content))
        return path

    def test_a_missing_file_says_to_run_a_scan(self):
        with self.assertRaises(AuditError) as ctx:
            read_snapshot(os.path.join(self.tmpdir.name, "absent.json"))
        message = str(ctx.exception)
        self.assertIn("aws_resource_audit.py", message)
        self.assertIn("audit_config.json", message)

    def test_malformed_json_is_reported_as_such(self):
        with self.assertRaises(AuditError) as ctx:
            read_snapshot(self._write("{not json"))
        self.assertIn("could not be read", str(ctx.exception))

    def test_a_top_level_list_is_rejected(self):
        with self.assertRaises(AuditError) as ctx:
            read_snapshot(self._write([]))
        self.assertIn("expected a JSON object", str(ctx.exception))

    def test_a_future_schema_is_refused_rather_than_half_read(self):
        """A newer schema version is refused rather than half-rendered."""
        with self.assertRaises(AuditError) as ctx:
            read_snapshot(self._write({"schema_version": SCHEMA_VERSION + 1, "rows": []}))
        self.assertIn("Re-run aws_resource_audit.py", str(ctx.exception))

    def test_an_old_cost_figure_is_not_carried_forward_as_an_attribution(self):
        """Version 6 to 7: the old even-split cost becomes unavailable, with a re-scan hint."""
        row = {name: "" for name in SNAPSHOT_ROW_FIELDS if name != "cost"}
        row.update({"service": "EC2Instance", "region": "us-east-1",
                    "resource_id": "i-1", "est_monthly_cost_usd": "412.55",
                    "tags": {}, "shared_with": [], "project_candidates": [],
                    "activity": {}, "_references": []})
        snapshot = read_snapshot(self._write(
            {"schema_version": 6, "account": "111122223333", "rows": [row]}))

        self.assertEqual(snapshot.rows[0]["cost"]["state"], "unavailable")
        self.assertFalse(snapshot.billing.queried)
        cells = audit.cost_columns(snapshot.rows[0])
        self.assertEqual(cells["est_monthly_cost_usd"], "")
        self.assertIn("Re-scan", cells["cost_notes"])

    def test_a_v9_default_tier_reason_is_blanked_and_a_rule_reason_is_kept(self):
        """Version 10 stopped explaining a default tier; older scans stored a
        sentence there, and must lose it without a re-scan."""
        def row(rid, why):
            out = {name: "" for name in SNAPSHOT_ROW_FIELDS}
            out.update({"service": "S3Bucket", "region": "us-east-1",
                        "resource_id": rid, "tier": "runtime", "why_tier": why,
                        "tags": {}, "shared_with": [], "project_candidates": [],
                        "activity": {}, "cost": {}, "_references": []})
            return out
        legacy = ("no rule found evidence this is deployment-only machinery, "
                  "so it is assumed to be part of the running application")
        kept = "on a request path - something serving traffic links to it"
        snapshot = read_snapshot(self._write(
            {"schema_version": 9, "account": "111122223333",
             "rows": [row("a", legacy), row("b", kept)]}))

        self.assertEqual([r["why_tier"] for r in snapshot.rows], ["", kept])

    def test_a_snapshot_with_no_rows_key_is_refused(self):
        with self.assertRaises(AuditError) as ctx:
            read_snapshot(self._write({"schema_version": SCHEMA_VERSION}))
        self.assertIn("'rows'", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
