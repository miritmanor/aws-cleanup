"""The cost pass: a figure is never shown where nothing supports it, money never moves to
a row that did not incur it, and the parts add up to the total."""

import unittest
from decimal import Decimal

from .cost_helpers import (EC2_COMPUTE, EC2_TYPES, GLOBAL, ROUTE53, attributed, bill,
                           complete_inventory, with_usage, zone)
from .fakes import FakeSession, client_error, make_row

import aws_resource_audit as audit
from aws_resource_audit.analyze.cost import attribute_costs, total_spend
from aws_resource_audit.billing import BillingResult, not_queried
from aws_resource_audit.collect.cost import cache_clear, collect_billing
from aws_resource_audit.present.cost import cost_columns, render_billing_coverage, render_unallocated


class NotQueriedTests(unittest.TestCase):
    """--no-cost is a third answer beside "queried" and "failed". A blank in a
    money column reads as free, which is the one thing it must never say."""

    def test_no_cost_makes_no_billing_call_at_all(self):
        session = FakeSession({"ce": {}})
        result = collect_billing(session, no_cost=True)
        self.assertFalse(result.queried)
        self.assertEqual(session.get("ce").calls, [])

    def test_a_row_says_not_queried_rather_than_zero(self):
        cells = attributed([make_row("DynamoDBTable", "t1")], not_queried())
        self.assertEqual(cells[0]["est_monthly_cost_usd"], "")
        self.assertEqual(cells[0]["cost"], "not queried")
        self.assertIn("--no-cost", cells[0]["cost_notes"])

    def test_the_report_says_it_cannot_check_coverage(self):
        report = audit.assess_billing_coverage(not_queried())
        text = render_billing_coverage(report)
        self.assertIn("nothing to compare the inventory against", text)
        self.assertNotIn("0.00", text)


class CollectionTests(unittest.TestCase):
    def setUp(self):
        cache_clear()
        self.addCleanup(cache_clear)

    def _page(self, groups, token=None, estimated=False):
        page = {"ResultsByTime": [{"Estimated": estimated, "Groups": groups}]}
        if token:
            page["NextPageToken"] = token
        return page

    def _group(self, service, region, amount):
        return {"Keys": [service, region],
                "Metrics": {"UnblendedCost": {"Amount": amount, "Unit": "USD"}}}

    def test_every_page_is_read_not_just_the_first(self):
        """The bug this replaces: one request, and everything past the first
        page silently missing from the account's total."""
        pages = {
            None: self._page([self._group("AWS Lambda", "us-east-1", "1.00")],
                             token="page-2"),
            "page-2": self._page([self._group("Amazon DynamoDB", "us-east-1", "2.00")]),
        }
        session = FakeSession({"ce": {
            "get_cost_and_usage": lambda **kw: pages[kw.get("NextPageToken")]}})
        result = collect_billing(session)
        self.assertTrue(result.complete)
        self.assertEqual(result.total, Decimal("3.00"))

    def test_a_partial_failure_keeps_what_arrived_and_says_so(self):
        calls = []

        def respond(**kwargs):
            calls.append(kwargs.get("NextPageToken"))
            if kwargs.get("NextPageToken"):
                return client_error(operation="GetCostAndUsage")
            return self._page([self._group("AWS Lambda", "us-east-1", "1.00")],
                              token="page-2")

        session = FakeSession({"ce": {"get_cost_and_usage": respond}})
        result = collect_billing(session)
        self.assertFalse(result.complete)
        self.assertEqual(result.total, Decimal("1.00"))
        self.assertIn("AccessDenied", result.error)

    def test_aws_estimated_marker_is_carried_not_dropped(self):
        session = FakeSession({"ce": {"get_cost_and_usage": self._page(
            [self._group("AWS Lambda", "us-east-1", "1.00")], estimated=True)}})
        self.assertTrue(collect_billing(session).estimated)

    def test_a_charge_with_no_region_is_kept_without_inventing_one(self):
        session = FakeSession({"ce": {"get_cost_and_usage": self._page(
            [self._group("Tax", "NoRegion", "5.00")])}})
        result = collect_billing(session)
        self.assertEqual(result.amounts[0].region, "")

    def test_zero_and_credit_lines_are_preserved(self):
        """Net-positive cost is not a presence test, and a credit is real."""
        session = FakeSession({"ce": {"get_cost_and_usage": self._page([
            self._group("AWS Lambda", "us-east-1", "0"),
            self._group("Refund", "us-east-1", "-4.00")])}})
        services = collect_billing(session).by_service()
        self.assertEqual(services["AWS Lambda"], Decimal("0"))
        self.assertEqual(services["Refund"], Decimal("-4.00"))

    def test_a_usage_type_breakdown_is_only_asked_for_when_it_explains_something(self):
        """The second chargeable request. A bill with nothing broad in it must
        not pay for one."""
        session = FakeSession({"ce": {"get_cost_and_usage": self._page(
            [self._group("AWS Lambda", "us-east-1", "1.00")])}})
        collect_billing(session)
        self.assertEqual(len(session.get("ce").calls), 1)

    def test_a_broad_bucket_gets_its_usage_types(self):
        def respond(**kwargs):
            if "Filter" in kwargs:
                return {"ResultsByTime": [{"Groups": [{
                    "Keys": ["EC2 - Other", "USE1-NatGateway-Hours"],
                    "Metrics": {"UnblendedCost": {"Amount": "31.00", "Unit": "USD"}}}]}]}
            return self._page([self._group("EC2 - Other", "us-east-1", "40.00")])

        session = FakeSession({"ce": {"get_cost_and_usage": respond}})
        result = collect_billing(session)
        self.assertEqual([u.usage_type for u in result.usage_types_for("EC2 - Other")],
                         ["USE1-NatGateway-Hours"])


class CachingTests(unittest.TestCase):
    def setUp(self):
        cache_clear()
        self.addCleanup(cache_clear)

    def _session(self):
        return FakeSession({"ce": {"get_cost_and_usage": {"ResultsByTime": [
            {"Groups": [{"Keys": ["AWS Lambda", "us-east-1"],
                         "Metrics": {"UnblendedCost": {"Amount": "1.00",
                                                       "Unit": "USD"}}}]}]}}})

    def test_a_new_scan_always_pays_for_its_own_figures(self):
        """A scan never reuses an earlier scan's billing figures."""
        session = self._session()
        collect_billing(session, account="111111111111")
        collect_billing(session, account="111111111111")
        self.assertEqual(len(session.get("ce").calls), 2)

    def test_a_repeat_within_one_scan_is_not_paid_for_twice(self):
        """The only thing the cache is still for: the answer cannot have
        changed, and the request is chargeable."""
        session = self._session()
        collect_billing(session, account="111111111111")
        collect_billing(session, account="111111111111", refresh=False)
        self.assertEqual(len(session.get("ce").calls), 1)

    def test_a_cache_entry_never_crosses_an_account_boundary(self):
        """Not a stale number - someone else's money, rendered against this
        account's inventory."""
        session = self._session()
        collect_billing(session, account="111111111111")
        second = collect_billing(session, account="222222222222", refresh=False)
        self.assertEqual(len(session.get("ce").calls), 2)
        self.assertEqual(second.account, "222222222222")

    def test_an_incomplete_response_is_never_cached(self):
        def respond(**kwargs):
            return client_error(operation="GetCostAndUsage")

        session = FakeSession({"ce": {"get_cost_and_usage": respond}})
        collect_billing(session, account="111111111111")
        collect_billing(session, account="111111111111", refresh=False)
        self.assertEqual(len(session.get("ce").calls), 2)


class AttributionTests(unittest.TestCase):
    def test_a_divisible_bucket_is_split_and_labelled_an_estimate(self):
        rows = [make_row("EC2Instance", f"i-{n}") for n in range(4)]
        cells = attributed(rows, bill((EC2_COMPUTE, "us-east-1", "40.00")),
                           complete_inventory(*EC2_TYPES))
        self.assertEqual([c["est_monthly_cost_usd"] for c in cells], ["10.00"] * 4)
        self.assertEqual(cells[0]["cost"], "estimated share")

    def test_nothing_is_ever_reported_as_measured(self):
        """DIRECT is reserved in the schema and produced by nothing: no report
        may imply a division was a measurement."""
        rows = [make_row("EC2Instance", "i-1")]
        report = attribute_costs(rows, bill((EC2_COMPUTE, "us-east-1", "9.00")),
                                 coverage_entries=complete_inventory(*EC2_TYPES))
        self.assertNotEqual(rows[0]["cost"]["state"], "direct")
        self.assertTrue(all(b.state != "direct" for b in report.buckets))

    def test_another_regions_spending_is_not_handed_to_scanned_rows(self):
        """Scan one region and the old code gave that region's rows every
        region's bill."""
        rows = [make_row("EC2Instance", "i-1", region="us-east-1")]
        report = attribute_costs(
            rows, bill((EC2_COMPUTE, "us-east-1", "10.00"),
                       (EC2_COMPUTE, "eu-west-1", "90.00")),
            coverage_entries=complete_inventory(*EC2_TYPES))
        self.assertEqual(cost_columns(rows[0])["est_monthly_cost_usd"], "10.00")
        self.assertEqual(report.allocated, Decimal("10.00"))
        self.assertEqual(report.unallocated, Decimal("90.00"))

    def test_a_deleted_resources_cost_is_not_redistributed(self):
        """The same bill, one fewer row: the remaining row keeps its own share
        and the rest stays unallocated rather than inflating it."""
        one_row = [make_row("EC2Instance", "i-1")]
        report = attribute_costs(
            one_row, bill((EC2_COMPUTE, "us-east-1", "40.00")),
            coverage_entries=complete_inventory(*EC2_TYPES))
        self.assertEqual(cost_columns(one_row[0])["est_monthly_cost_usd"], "40.00")
        # ... and with the bucket no longer divisible, nothing moves at all.
        rows = [make_row("EBSVolume", "vol-1")]
        report = attribute_costs(rows, bill(("EC2 - Other", "us-east-1", "40.00")),
                                 coverage_entries=complete_inventory("EBSVolume"))
        self.assertEqual(report.allocated, Decimal("0"))

    def test_a_partially_covered_bucket_is_not_divided(self):
        """"EC2 - Other" also bills data transfer, so it is not split across EBS volumes."""
        rows = [make_row("EBSVolume", "vol-1")]
        cells = attributed(rows, bill(("EC2 - Other", "us-east-1", "40.00")),
                           complete_inventory("EBSVolume", "EBSSnapshot",
                                              "ElasticIP", "AMI", "NatGateway"))
        self.assertEqual(cells[0]["est_monthly_cost_usd"], "")
        self.assertEqual(cells[0]["cost"], "unallocated (shared bill)")
        self.assertIn("data transfer", cells[0]["cost_notes"])

    def test_a_denied_collector_stops_its_bucket_being_divided(self):
        rows = [make_row("EC2Instance", "i-1")]
        cells = attributed(rows, bill((EC2_COMPUTE, "us-east-1", "40.00")),
                           [{"service": "EC2Instance", "capability": "inventory",
                             "status": "denied", "scope": "us-east-1"}])
        self.assertEqual(cells[0]["cost"], "unallocated (shared bill)")
        self.assertIn("refused or cut short", cells[0]["cost_notes"])

    def test_a_shared_resource_is_counted_once_and_charged_to_nobody(self):
        shared = make_row("EC2Instance", "i-shared")
        shared["membership"] = "shared"
        rows = [shared, make_row("EC2Instance", "i-1")]
        report = attribute_costs(rows, bill((EC2_COMPUTE, "us-east-1", "10.00")),
                                 coverage_entries=complete_inventory(*EC2_TYPES))
        self.assertEqual(cost_columns(shared)["est_monthly_cost_usd"], "")
        self.assertEqual(cost_columns(rows[1])["est_monthly_cost_usd"], "5.00")
        self.assertEqual(report.allocated, Decimal("5.00"))
        self.assertEqual(report.unallocated, Decimal("5.00"))

    def test_unknown_cost_is_not_shown_as_zero(self):
        """A type no billing table mentions reads "unknown", never free."""
        cells = attributed([make_row("NetworkInterface", "eni-1")], bill())
        self.assertEqual(cells[0]["est_monthly_cost_usd"], "")
        self.assertEqual(cells[0]["cost"], "unknown")

    def test_an_incomplete_response_makes_absence_prove_nothing(self):
        """Pages arrived, and then stopped. A type with no charge among them
        might have had one on the page that never came."""
        cells = attributed([make_row("EC2Instance", "i-1", region="us-east-1")],
                           bill((EC2_COMPUTE, "eu-west-1", "1.00"),
                                complete=False, error="throttled"))
        self.assertEqual(cells[0]["cost"], "unknown")
        self.assertIn("incomplete", cells[0]["cost_notes"])

    def test_a_mapped_service_the_bill_did_not_mention_reads_as_zero(self):
        """A complete response that names no charge for a type is evidence of
        zero, which is a different answer from "unknown" and must stay one."""
        cells = attributed([make_row("EC2Instance", "i-1")], bill())
        self.assertEqual(cells[0]["est_monthly_cost_usd"], "0.00")
        self.assertEqual(cells[0]["cost"], "estimated share")

    def test_a_failed_lookup_is_written_onto_every_row(self):
        cells = attributed([make_row("EC2Instance", "i-1"),
                            make_row("S3Bucket", "b1")],
                           bill(error="AccessDenied: ce:GetCostAndUsage"))
        for cell in cells:
            self.assertEqual(cell["cost"], "unknown")
            self.assertIn("AccessDenied", cell["cost_notes"])

    def test_totals_reconcile(self):
        rows = [make_row("EC2Instance", "i-1"), make_row("EBSVolume", "vol-1")]
        report = attribute_costs(
            rows, bill((EC2_COMPUTE, "us-east-1", "10.00"),
                       ("EC2 - Other", "us-east-1", "40.00"),
                       ("Amazon Lex", "us-east-1", "7.00")),
            coverage_entries=complete_inventory(*EC2_TYPES))
        self.assertTrue(report.reconciles())
        self.assertEqual(report.allocated + report.unallocated, Decimal("57.00"))

    def test_total_spend_is_none_rather_than_zero_when_never_asked(self):
        self.assertIsNone(total_spend(not_queried()))


class AccountWideTests(unittest.TestCase):
    """Global rows against charges with no region: a billed hosted zone is never 0.00."""

    def test_a_billed_zone_is_never_shown_as_zero(self):
        cells = attributed([zone("Z1")], bill((ROUTE53, "", "0.50")), GLOBAL)
        self.assertEqual(cells[0]["est_monthly_cost_usd"], "")
        self.assertEqual(cells[0]["cost"], "unallocated (shared bill)")
        self.assertIn("DNS queries", cells[0]["cost_notes"])

    def test_the_hosted_zone_line_is_divided_across_the_zones(self):
        rows = [zone("Z1"), zone("Z2")]
        billing = with_usage(bill((ROUTE53, "", "2.50")),
                             (ROUTE53, "HostedZone", "1.00"),
                             (ROUTE53, "DNS-Queries", "1.50"))
        report = attribute_costs(rows, billing, coverage_entries=GLOBAL)
        cells = [cost_columns(r) for r in rows]
        self.assertEqual([c["est_monthly_cost_usd"] for c in cells], ["0.50", "0.50"])
        self.assertEqual(cells[0]["cost"], "estimated share")
        self.assertIn("HostedZone line", cells[0]["cost_notes"])
        self.assertEqual(report.allocated, Decimal("1.00"))
        self.assertEqual(report.unallocated, Decimal("1.50"))
        self.assertTrue(report.reconciles())
        # The query charges are a refusal, not what was left over from rounding.
        self.assertNotIn("rounding", render_unallocated(report))

    def test_the_zone_line_is_not_divided_when_the_zone_lookup_was_denied(self):
        billing = with_usage(bill((ROUTE53, "", "0.50")),
                             (ROUTE53, "HostedZone", "0.50"))
        cells = attributed([zone("Z1")], billing,
                           [{"service": "Route53HostedZone", "capability": "inventory",
                             "status": "denied", "scope": "global"}])
        self.assertEqual(cells[0]["est_monthly_cost_usd"], "")
        self.assertIn("refused or cut short", cells[0]["cost_notes"])

    def test_a_zone_shared_by_two_projects_is_charged_to_nobody(self):
        shared = zone("Z1")
        shared["membership"] = "shared"
        rows = [shared, zone("Z2")]
        billing = with_usage(bill((ROUTE53, "", "1.00")),
                             (ROUTE53, "HostedZone", "1.00"))
        report = attribute_costs(rows, billing, coverage_entries=GLOBAL)
        self.assertEqual(cost_columns(shared)["est_monthly_cost_usd"], "")
        self.assertEqual(cost_columns(rows[1])["est_monthly_cost_usd"], "0.50")
        self.assertTrue(report.reconciles())

    def test_a_no_region_charge_with_no_gaps_is_divided_across_global_rows(self):
        rows = [make_row("CloudFrontDistribution", f"E{n}", region="global")
                for n in range(2)]
        cells = attributed(rows, bill(("Amazon CloudFront", "", "3.00")), GLOBAL)
        self.assertEqual([c["est_monthly_cost_usd"] for c in cells], ["1.50", "1.50"])

    def test_a_no_region_charge_still_needs_a_global_row(self):
        """An EC2 charge AWS gives no region is not handed to a regional instance."""
        rows = [make_row("EC2Instance", "i-1")]
        report = attribute_costs(rows, bill((EC2_COMPUTE, "", "9.00")),
                                 coverage_entries=complete_inventory(*EC2_TYPES))
        self.assertEqual(report.allocated, Decimal("0"))

    def test_an_old_snapshots_global_bucket_is_not_zero_when_s3_is_billed_by_region(self):
        """Snapshots before decision 0062 record buckets as global; unplaced, not free."""
        s3 = "Amazon Simple Storage Service"
        cells = attributed([make_row("S3Bucket", "b1", region="global")],
                           bill((s3, "us-east-1", "5.00")), GLOBAL)
        self.assertEqual(cells[0]["est_monthly_cost_usd"], "")
        self.assertEqual(cells[0]["cost"], "unallocated (shared bill)")

    def test_a_small_no_region_line_is_not_passed_off_as_an_old_global_buckets_cost(self):
        s3 = "Amazon Simple Storage Service"
        report = attribute_costs(
            [make_row("S3Bucket", "b1", region="global")],
            bill((s3, "us-east-1", "5.00"), (s3, "", "0.02")),
            coverage_entries=GLOBAL)
        self.assertEqual(report.allocated, Decimal("0"))

    def test_a_global_row_the_bill_never_mentions_still_reads_as_zero(self):
        cells = attributed([zone("Z1")], bill(), GLOBAL)
        self.assertEqual(cells[0]["est_monthly_cost_usd"], "0.00")
