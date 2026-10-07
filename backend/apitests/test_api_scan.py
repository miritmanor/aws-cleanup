"""The scan's payload over HTTP: health, the missing-snapshot answers, the bill,
rows and graph, and the downloads."""

import json
import os
import tempfile
from decimal import Decimal
import unittest
from . import _REPO  # noqa: F401  (import for the sys.path fixup side effect)
import aws_resource_audit as audit
from aws_resource_audit.present.csv import CSV_FIELDNAMES
from aws_resource_audit.rows import ROW_FIELDS
from .api_base import (
    ApiTestCase,
    _fresh_app,
)


class HealthTests(ApiTestCase):
    def test_health_does_not_need_a_scan(self):
        """A server with no scan yet is healthy; health never reads the snapshot."""
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")


class MissingSnapshotTests(ApiTestCase):
    def test_the_scan_endpoint_says_what_to_do(self):
        """Opening the app before scanning: 404 with the core's own sentence."""
        response = self.client.get("/api/scan")
        self.assertEqual(response.status_code, 404)
        self.assertIn("Run a scan first", response.json()["detail"])

    def test_groups_still_list_without_a_scan(self):
        """Names outlive scans. Refusing to list them because there is no
        current scan would lose the user's work behind an unrelated failure."""
        response = self.client.get("/api/groups")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])


class BillingTests(ApiTestCase):
    """The billing endpoint serves decisions and sentences, not raw figures for
    TypeScript to re-derive."""

    def setUp(self):
        super().setUp()
        self.write_snapshot()

    def test_a_billed_service_nothing_collects_arrives_as_a_finding(self):
        body = self.client.get("/api/billing").json()
        unsupported = [s["service"] for s in body["services"]
                       if s["state"] == "unsupported"]
        self.assertIn("Amazon Lex", unsupported)

    def test_the_caveats_are_served_rather_than_restated_in_the_client(self):
        body = self.client.get("/api/billing").json()
        self.assertIn("Recorded spend", body["period_note"])

    def test_a_service_charged_nothing_never_reaches_the_client(self):
        """The client filters on state alone, so a zero-cost service served
        here would be listed as money the report cannot account for."""
        body = self.client.get("/api/billing").json()
        self.assertTrue(all(Decimal(s["amount"]) >= Decimal("0.01")
                            for s in body["services"]
                            if s["state"] in ("unsupported", "blocked",
                                              "not_found", "partial")))

    def test_amounts_are_strings_and_reconcile(self):
        body = self.client.get("/api/billing").json()
        self.assertTrue(body["reconciles"])
        self.assertEqual(Decimal(body["allocated"]) + Decimal(body["unallocated"]),
                         Decimal(body["total"]))

    def test_a_rows_cost_is_re_derived_on_load_not_read_from_the_scan(self):
        """A scan stores each row's cost. A rule fixed since then must reach
        the table without a re-scan, as it does in the CLI's re-render."""
        paths = audit.output_paths(audit.Settings(output_dir=self.data_dir))
        with open(paths["snapshot"]) as f:
            data = json.load(f)
        for row in data["rows"]:
            row["cost"] = {"state": "estimated", "amount": "999.00",
                           "explanation": "stored by an older scan"}
        with open(paths["snapshot"], "w") as f:
            json.dump(data, f)

        rows = self.client.get("/api/scan").json()["rows"]
        self.assertTrue(rows)
        self.assertFalse([r for r in rows if r["est_monthly_cost_usd"] == "999.00"])

    def test_no_scan_is_a_404_not_an_empty_bill(self):
        """An empty bill would read as "this account spent nothing", which is
        exactly the confusion the not-queried state exists to prevent."""
        with tempfile.TemporaryDirectory() as empty:
            client = _fresh_app(empty)
            self.assertEqual(client.get("/api/billing").status_code, 404)


class ScanPayloadTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.rows = self.write_snapshot()

    def test_every_row_is_returned(self):
        response = self.client.get("/api/scan")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["row_count"], len(self.rows))
        self.assertEqual(len(body["rows"]), len(self.rows))
        self.assertEqual(body["account"], "123456789012")

    def test_rows_carry_exactly_the_public_schema(self):
        """display_row() emits exactly ROW_FIELDS, so no internal field reaches a
        client. Asserted against ROW_FIELDS itself."""
        body = self.client.get("/api/scan").json()
        expected = set(ROW_FIELDS) | {"console_url"}
        for row in body["rows"]:
            self.assertEqual(set(row), expected)

    def test_no_internal_field_leaks(self):
        """_references, the one internal field the snapshot carries, never reaches the wire."""
        body = self.client.get("/api/scan").json()
        for row in body["rows"]:
            for key in row:
                self.assertFalse(key.startswith("_"),
                                 f"internal field {key} reached the client")

    def test_the_graph_is_contracted_by_default(self):
        """contract_graph runs on the render side so that "draw everything" is a
        query parameter rather than a re-scan."""
        contracted = self.client.get("/api/graph").json()
        every = self.client.get("/api/graph", params={"all_nodes": True}).json()
        self.assertLess(len(contracted["nodes"]), len(every["nodes"]))
        self.assertIn("stats", contracted)

    def test_service_meta_is_served_not_duplicated(self):
        """Every service in the scan has a colour in the served table."""
        meta = self.client.get("/api/service-meta").json()
        services = {row["service"] for row in self.client.get("/api/scan").json()["rows"]}
        self.assertTrue(services <= set(meta), sorted(services - set(meta)))

    def test_mermaid_is_rendered_not_duplicated(self):
        """The diagram is render_mermaid() called server-side: pins the contract
        (an AWS-icon flowchart), not the layout."""
        text = self.client.get("/api/mermaid").json()["mermaid"]
        self.assertIn("flowchart", text)
        self.assertIn('icon: "aws:', text)
        self.assertNotIn("Administrative resources", text)

    def test_mermaid_draws_the_architecture_graph(self):
        """The diagram is architecture_graph, runtime nodes only,
        so an admin resource (IAM role, security group) never appears in it."""
        paths = audit.output_paths(audit.Settings(output_dir=self.data_dir))
        snapshot = audit.read_snapshot(paths["snapshot"])
        expected = audit.architecture_graph(snapshot.graph or {}, snapshot.rows)
        mermaid_text = self.client.get("/api/mermaid").json()["mermaid"]
        self.assertEqual(len(expected["nodes"]), mermaid_text.count("<br/>"))
        self.assertNotIn("<br/>IAMRole", mermaid_text)
        self.assertNotIn("<br/>SecurityGroup", mermaid_text)


class ExportTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.rows = self.write_snapshot()

    def test_the_csv_download_is_the_renderers_own_columns(self):
        """Served through present/csv.py rather than assembled in the browser,
        so the download and aws_inventory.csv cannot have different columns."""
        response = self.client.get("/api/export/inventory.csv")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/csv"))
        self.assertIn("attachment", response.headers["content-disposition"])
        header = response.text.splitlines()[0].split(",")
        self.assertEqual(header, CSV_FIELDNAMES)
        self.assertEqual(len(response.text.splitlines()), len(self.rows) + 1)

    def test_the_mermaid_download_matches_what_the_tab_renders(self):
        """One render_mermaid() call behind both, so the file a person saves is
        the diagram they were just looking at."""
        response = self.client.get("/api/export/architecture.mmd")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertEqual(response.text, self.client.get("/api/mermaid").json()["mermaid"])

    def test_both_downloads_need_a_scan(self):
        """404 rather than an empty file: no scan yet is the first-run state."""
        paths = audit.output_paths(audit.Settings(output_dir=self.data_dir))
        os.remove(paths["snapshot"])
        from app import stores
        stores.reset()
        for path in ("/api/export/inventory.csv", "/api/export/architecture.mmd"):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_the_removed_file_listing_is_gone(self):
        """The old file-listing endpoints are gone."""
        self.assertEqual(self.client.get("/api/files").status_code, 404)
        self.assertEqual(self.client.post("/api/render").status_code, 404)


if __name__ == "__main__":
    unittest.main()
