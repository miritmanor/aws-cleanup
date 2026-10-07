"""Byte-for-byte renders of the synthetic corpus. Regenerate with UPDATE_GOLDEN=1
only when the output change is the point, and read the diff before committing."""

import os
import tempfile
import unittest
from datetime import datetime

from . import corpus
from .corpus import ALL_SERVICES

import aws_resource_audit as audit

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")
UPDATING = os.environ.get("UPDATE_GOLDEN") == "1"


def render_csv(rows):
    """The writer render_outputs() uses, with the golden file's line ending."""
    return audit.render_csv(rows, lineterminator="\n")


class GoldenRenderTests(unittest.TestCase):
    maxDiff = 4000

    @classmethod
    def setUpClass(cls):
        cls.rows, cls.contracted, cls.full, cls.notes = corpus.build()
        cls.billing = corpus.billing()
        cls.coverage = corpus.coverage_entries()
        cls.cost_report = audit.attribute_costs(
            cls.rows, cls.billing, coverage_entries=cls.coverage)
        cls.billing_coverage = audit.assess_billing_coverage(
            cls.billing, coverage_entries=cls.coverage, rows=cls.rows)

    def assertMatchesGolden(self, name, actual):
        path = os.path.join(GOLDEN_DIR, name)
        if UPDATING:
            os.makedirs(GOLDEN_DIR, exist_ok=True)
            with open(path, "w") as handle:
                handle.write(actual)
            self.skipTest(f"rewrote {name}")
        with open(path) as handle:
            expected = handle.read()
        if expected != actual:
            self.fail(
                f"{name} does not match the golden file.\n"
                f"  expected {len(expected)} chars, got {len(actual)}\n"
                f"If the change is intended, regenerate with:\n"
                f"  UPDATE_GOLDEN=1 /usr/bin/python3 -m unittest tests.test_golden\n"
                f"and review the diff before committing."
            )

    def test_csv(self):
        self.assertMatchesGolden("inventory.csv", render_csv(self.rows))

    def overview(self):
        """The overview payload; built here so the no-panel render can leave it out."""
        return audit.overview_payload(
            audit.summarize_projects(self.rows),
            audit.summarize_services(self.rows, self.cost_report.billing),
            default_metric="resource_count", account="111122223333")

    def test_html(self):
        self.assertMatchesGolden("report.html", audit.render_html_report(
            self.rows, graph=self.contracted, coverage=self.coverage,
            diagram_graph=audit.architecture_graph(self.full, self.rows),
            billing_coverage=self.billing_coverage,
            cost_report=self.cost_report,
            overview=self.overview()))

    def test_html_without_the_graph_panel(self):
        """graph=None is a separate render path: it drops the CDN script tag and
        every graph asset, so it can break on its own."""
        self.assertMatchesGolden("report_nograph.html", audit.render_html_report(
            self.rows, graph=None, coverage=self.coverage,
            billing_coverage=self.billing_coverage,
            cost_report=self.cost_report,
            overview=self.overview()))

    def test_html_without_the_project_overview(self):
        """overview=None omits the overview panel, its CSS and its script."""
        page = audit.render_html_report(
            self.rows, graph=None, coverage=self.coverage,
            billing_coverage=self.billing_coverage,
            cost_report=self.cost_report)
        self.assertNotIn("bubblesWrap", page)
        self.assertNotIn("bubbles-wrap", page)
        self.assertIn("const BUBBLES = null;", page)

    def test_json(self):
        """Goldened as well as schema-tested: test_json.py says which fields
        are emitted, this says what they contain and in what order."""
        self.assertMatchesGolden("inventory.json", audit.render_json(self.rows))

    def test_drawio(self):
        """The same architecture graph as an editable draw.io file."""
        self.assertMatchesGolden("graph.drawio", audit.render_drawio(
            audit.architecture_graph(self.full, self.rows), self.rows))

    def test_mermaid(self):
        """The architecture graph as text: the .mmd and the Diagram tab."""
        self.assertMatchesGolden("graph.mmd", audit.render_mermaid(
            audit.architecture_graph(self.full, self.rows), self.rows,
            title="AWS dependencies"))

    def test_grouping_audit(self):
        """Two steps now: analyze/group_audit.py finds, present/markdown.py words."""
        findings = audit.audit_groups(self.rows, notes=self.notes)
        self.assertMatchesGolden("grouping_audit.md",
                                 audit.render_grouping_audit(
                                     findings, coverage=self.coverage,
                                     billing_coverage=self.billing_coverage,
                                     cost_report=self.cost_report))


class SnapshotRoundTripTests(GoldenRenderTests):
    """Every golden again, rendered from rows that went through the snapshot file, so
    anything the snapshot fails to carry shows as a diff."""

    @classmethod
    def setUpClass(cls):
        rows, _contracted, full, notes = corpus.build()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "aws_scan.json")
            # scanned_at is the pinned clock, which is also what now() returns
            # for the in-memory renders - so the HTML header is comparable.
            audit.write_snapshot(path, rows, full, notes, account="123456789012",
                                 regions=["us-east-1"], scanned_at=audit.now(),
                                 coverage=corpus.coverage_entries(),
                                 billing=corpus.billing())
            snapshot = audit.read_snapshot(path)
        cls.snapshot = snapshot
        cls.rows = snapshot.rows
        cls.full = snapshot.graph
        cls.contracted = audit.contract_graph(snapshot.graph)
        cls.notes = snapshot.grouping
        # Re-derived from the snapshot exactly as render_outputs() does.
        cls.billing = snapshot.billing
        cls.coverage = snapshot.coverage
        cls.cost_report = audit.attribute_costs(
            cls.rows, snapshot.billing, coverage_entries=snapshot.coverage)
        cls.billing_coverage = audit.assess_billing_coverage(
            snapshot.billing, coverage_entries=snapshot.coverage, rows=cls.rows)

    def test_the_round_trip_actually_happened(self):
        """Guards the guard. If setUpClass silently handed back the original
        rows, every inherited test above would pass while testing nothing."""
        self.assertIsNot(self.rows, corpus.build()[0])
        self.assertTrue(self.snapshot.scanned_at, "no scan time survived")
        self.assertEqual(self.snapshot.account, "123456789012")

    def test_dates_come_back_as_values_not_text(self):
        """Dates survive the round trip as datetimes (format.py raises on strings)."""
        dated = [r for r in self.rows if r["created"]]
        self.assertTrue(dated, "corpus has no dated rows; this proves nothing")
        for row in dated:
            self.assertIsInstance(row["created"], datetime, row["resource_id"])


class CorpusCoverageTests(unittest.TestCase):
    """The goldens are only as good as what the corpus contains."""

    def test_the_corpus_covers_every_service(self):
        """A new collector must appear in the goldens."""
        rows, _, _, _ = corpus.build()
        self.assertEqual(sorted({r["service"] for r in rows}), sorted(ALL_SERVICES))

    def test_the_corpus_service_list_matches_the_collector_guard(self):
        """Two lists of every service would drift. Pin them together instead."""
        from .test_collectors_schema import SchemaAndFailureTests
        self.assertEqual(sorted(ALL_SERVICES),
                         sorted(SchemaAndFailureTests.ALL_SERVICES))

    def test_the_corpus_exercises_the_awkward_cases(self):
        """Each distinct output branch still appears in the corpus."""
        rows, contracted, full, notes = corpus.build()
        connections = " ".join(audit.render_connections(r) for r in rows)

        self.assertIn("DANGLING ->", connections)
        self.assertIn("coincidental name collision", connections)
        self.assertIn("report-only", connections)
        self.assertIn("used by", connections)
        # No ERROR row: failed lookups never reach the rendering side.
        self.assertFalse(any(r["flag"] == "ERROR" for r in rows))
        costs = [audit.cost_columns(r)["cost"] for r in rows]
        self.assertIn("estimated share", costs)
        self.assertIn("unallocated (shared bill)", costs)
        self.assertTrue(any(n["kind"] == "stub" for n in full["nodes"]),
                        "no dangling stub node in the graph")
        self.assertTrue(any(e.get("bridged") for e in contracted["edges"]),
                        "no bridged edge - the one link whose endpoints' "
                        "connections columns never mention each other, and the "
                        "single deliberate exception to graph/column agreement")
        self.assertTrue(contracted["stats"]["hidden_nodes"],
                        "contraction hid nothing, so the contracted golden "
                        "is the same graph as the full one")


if __name__ == "__main__":
    unittest.main()
