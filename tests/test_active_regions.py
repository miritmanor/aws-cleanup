"""Which regions count as active, how the list is saved, and how it is used."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from .fakes import FakeSession, make_row

import aws_resource_audit as audit
from aws_resource_audit.analyze.active_regions import find_active_regions
from aws_resource_audit.billing import BillingAmount, BillingResult
from aws_resource_audit.collect.regions import get_regions
from aws_resource_audit.present.regions import region_summary_lines
from aws_resource_audit.region_store import load_active_regions, record_active_regions

SCANNED = ["eu-west-1", "us-east-1", "us-west-2"]
WHEN = datetime(2026, 10, 5, tzinfo=timezone.utc)


def bill(*cells):
    return BillingResult(queried=True, currency="USD", amounts=tuple(
        BillingAmount(s, r, Decimal(a)) for s, r, a in cells))


class FindActiveRegionsTests(unittest.TestCase):
    def test_a_region_with_a_row_is_active_and_global_rows_are_not_a_region(self):
        rows = [make_row("LambdaFunction", "fn", region="us-east-1"),
                make_row("S3Bucket", "b", region="global")]
        active = find_active_regions(rows, SCANNED)
        self.assertEqual(active.resources, {"us-east-1": 1})
        self.assertEqual(active.regions, ["us-east-1"])
        self.assertEqual(active.empty_scanned, ["eu-west-1", "us-west-2"])

    def test_a_billed_region_counts_even_with_nothing_found_or_scanned(self):
        active = find_active_regions([], SCANNED, billing=bill(
            ("Amazon Lex", "eu-west-1", "1.20"), ("Amazon Lex", "ap-south-1", "0.40")))
        self.assertEqual(active.regions, ["ap-south-1", "eu-west-1"])
        self.assertEqual(active.billed_not_scanned, ["ap-south-1"])

    def test_a_sub_cent_or_no_region_charge_does_not_make_a_region_active(self):
        active = find_active_regions([], SCANNED, billing=bill(
            ("Amazon Lex", "eu-west-1", "0.004"), ("Amazon Route 53", "", "0.50")))
        self.assertEqual(active.regions, [])

    def test_a_failed_lookup_keeps_the_region(self):
        entries = [{"capability": "inventory", "status": "denied", "scope": "us-west-2"},
                   {"capability": "metrics", "status": "denied", "scope": "eu-west-1"}]
        active = find_active_regions([], SCANNED, coverage_entries=entries)
        self.assertEqual(active.unknown, ["us-west-2"])
        self.assertEqual(active.regions, ["us-west-2"])

    def test_the_summary_names_each_kind(self):
        active = find_active_regions(
            [make_row("LambdaFunction", "fn", region="us-east-1")], SCANNED,
            coverage_entries=[{"capability": "inventory", "status": "failed",
                               "scope": "us-west-2"}],
            billing=bill(("Amazon Lex", "ap-south-1", "2.00")))
        text = "\n".join(region_summary_lines(active))
        self.assertIn("Regions with resources: us-east-1 (1)", text)
        self.assertIn("did not cover: ap-south-1", text)
        self.assertIn("kept in the list: us-west-2", text)
        self.assertIn("nothing found in 1 other", text)


class RegionStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "aws_active_regions.json")

    def test_nothing_saved_reads_as_none(self):
        self.assertIsNone(load_active_regions(self.path, "111"))

    def test_an_all_regions_scan_replaces_and_any_other_scan_only_adds(self):
        record_active_regions(self.path, "111", ["us-east-1", "eu-west-1"],
                              replace=True, observed_at=WHEN)
        record_active_regions(self.path, "111", ["ap-south-1"], replace=False,
                              observed_at=WHEN)
        self.assertEqual(load_active_regions(self.path, "111")["regions"],
                         ["ap-south-1", "eu-west-1", "us-east-1"])
        record_active_regions(self.path, "111", ["us-east-1"], replace=True,
                              observed_at=WHEN)
        saved = load_active_regions(self.path, "111")
        self.assertEqual(saved["regions"], ["us-east-1"])
        self.assertTrue(saved["all_regions_scan_at"].startswith("2026-10-05"))

    def test_each_account_has_its_own_list(self):
        record_active_regions(self.path, "111", ["us-east-1"], replace=True, observed_at=WHEN)
        record_active_regions(self.path, "222", ["eu-west-1"], replace=True, observed_at=WHEN)
        self.assertEqual(load_active_regions(self.path, "111")["regions"], ["us-east-1"])
        self.assertEqual(load_active_regions(self.path, "222")["regions"], ["eu-west-1"])

    def test_an_empty_list_is_no_default(self):
        record_active_regions(self.path, "111", [], replace=True, observed_at=WHEN)
        self.assertIsNone(load_active_regions(self.path, "111"))

    def test_an_unreadable_file_is_no_default_not_a_failed_scan(self):
        with open(self.path, "w") as f:
            f.write("{not json")
        with self.assertLogs("aws_resource_audit.region_store", "WARNING"):
            self.assertIsNone(load_active_regions(self.path, "111"))

    def test_the_file_is_plain_json_keyed_by_account(self):
        record_active_regions(self.path, "111", ["us-east-1"], replace=True, observed_at=WHEN)
        with open(self.path) as f:
            data = json.load(f)
        self.assertEqual(data["accounts"]["111"]["regions"], ["us-east-1"])


class GetRegionsTests(unittest.TestCase):
    """explicit > all regions > saved list > the session's region > us-east-1."""

    def setUp(self):
        quiet = audit.console.set_quiet(True)
        self.addCleanup(audit.console.set_quiet, quiet)

    def session(self, region=None):
        session = FakeSession({"ec2": {"describe_regions": {"Regions": [
            {"RegionName": "us-east-1"}, {"RegionName": "eu-west-1"}]}}})
        session.region_name = region
        return session

    def test_the_order_of_precedence(self):
        saved = ["eu-west-1", "us-east-1"]
        self.assertEqual(get_regions(self.session(), ["ap-south-1"], False, saved), ["ap-south-1"])
        self.assertEqual(get_regions(self.session(), None, True, ["x"]), ["us-east-1", "eu-west-1"])
        self.assertEqual(get_regions(self.session("us-west-2"), None, False, saved), saved)
        self.assertEqual(get_regions(self.session("us-west-2"), None, False, None), ["us-west-2"])
        self.assertEqual(get_regions(self.session(), None, False, None), ["us-east-1"])


if __name__ == "__main__":
    unittest.main()
