"""The bill checked against the scan (billing coverage), and how cost findings are worded."""

import unittest
from decimal import Decimal

from .cost_helpers import (EC2_COMPUTE, EC2_TYPES, PERIOD_END, PERIOD_START, ROUTE53, bill,
                           complete_inventory, zone)
from .fakes import make_row

import aws_resource_audit as audit
from aws_resource_audit.analyze.cost import attribute_costs
from aws_resource_audit.billing import BillingResult
from aws_resource_audit.present.cost import (
    below_threshold_note,
    cost_columns, render_billing_coverage, render_unallocated,
)


class BillingCoverageTests(unittest.TestCase):
    def test_a_billed_service_nothing_collects_is_reported_with_no_rows(self):
        report = audit.assess_billing_coverage(
            bill(("Amazon Lex", "us-east-1", "12.34")))
        self.assertEqual([s.service for s in report.in_state("unsupported")],
                         ["Amazon Lex"])
        self.assertIn("Amazon Lex",
                      render_billing_coverage(report))

    def test_ec2_other_reads_as_partial_while_data_transfer_is_a_gap(self):
        report = audit.assess_billing_coverage(
            bill(("EC2 - Other", "us-east-1", "40.00")),
            coverage_entries=complete_inventory("EBSVolume", "EBSSnapshot",
                                                "ElasticIP", "AMI", "NatGateway"),
            rows=[make_row("EBSVolume", "vol-1")])
        entry = report.in_state("partial")[0]
        self.assertEqual(entry.service, "EC2 - Other")
        self.assertEqual(entry.gaps, ("inter-AZ and internet data transfer",))
        self.assertEqual(entry.row_count, 1)

    def test_being_charged_for_something_the_scan_found_none_of_is_a_finding(self):
        """Collected, not blocked, none found: the money is for something not in the inventory."""
        report = audit.assess_billing_coverage(
            bill(("EC2 - Other", "us-east-1", "40.00")),
            coverage_entries=complete_inventory("EBSVolume"))
        self.assertEqual([s.service for s in report.in_state("not_found")],
                         ["EC2 - Other"])

    def test_finding_none_of_a_type_is_not_reported_as_a_broken_collector(self):
        """A clean collector that found nothing records nothing; that is not a failure."""
        report = audit.assess_billing_coverage(
            bill((EC2_COMPUTE, "us-east-1", "10.00")),
            coverage_entries=complete_inventory("EC2Instance"),
            rows=[make_row("EC2Instance", "i-1")])
        self.assertEqual(report.in_state("blocked"), ())
        self.assertEqual([s.service for s in report.in_state("covered")],
                         [EC2_COMPUTE])

    def test_a_denied_collector_is_reported_separately_from_a_known_gap(self):
        report = audit.assess_billing_coverage(
            bill((EC2_COMPUTE, "us-east-1", "10.00")),
            coverage_entries=[{"service": s, "capability": "inventory",
                               "status": "denied", "scope": "us-east-1"}
                              for s in EC2_TYPES])
        self.assertEqual([s.service for s in report.in_state("blocked")],
                         [EC2_COMPUTE])

    def test_tax_is_listed_but_is_not_a_coverage_finding(self):
        report = audit.assess_billing_coverage(bill(("Tax", "", "5.00")))
        self.assertFalse(report.has_findings)
        self.assertIn("Tax", render_billing_coverage(report))

    def test_a_zero_line_is_kept_but_is_not_a_finding(self):
        """A service billed 0.00 stays in the data but is not a finding."""
        report = audit.assess_billing_coverage(
            bill(("Amazon Lex", "us-east-1", "0")))
        self.assertEqual(len(report.all_in_state("unsupported")), 1)
        self.assertEqual(report.in_state("unsupported"), ())
        self.assertFalse(report.has_findings)
        # Nor is it withheld: nothing was charged, so there is no money to
        # account for and "a further service would be listed" is not true.
        self.assertEqual(report.below_threshold_count(), 0)
        self.assertEqual(report.listed_services(), ())
        self.assertNotIn("cost less than 0.01", render_billing_coverage(report))

    def test_a_sub_cent_charge_is_counted_not_listed(self):
        """Six findings costing a thousandth of a cent buried the one that
        mattered. AWS itself rounds these to 0.00 on the bill."""
        report = audit.assess_billing_coverage(
            bill(("Amazon Athena", "", "0.5"),
                 ("Amazon QuickSight", "us-east-1", "0.00015"),
                 ("AWS Backup", "us-east-1", "0.000020224")))
        self.assertEqual([s.service for s in report.in_state("unsupported")],
                         ["Amazon Athena"])
        self.assertEqual(report.below_threshold_count(), 2)

    def test_a_hosted_zone_charge_is_explained_by_the_zone(self):
        """The 0.50 a month one zone costs is the zone the report lists, not a gap."""
        billing = bill(("Amazon Route 53", "", "0.50"))
        billing = BillingResult(
            **{**billing.__dict__,
               "usage_types": (audit.UsageTypeAmount("Amazon Route 53", "HostedZone",
                                                     Decimal("0.50")),)})
        report = audit.assess_billing_coverage(
            billing, coverage_entries=complete_inventory("Route53HostedZone"),
            rows=[make_row("Route53HostedZone", "Z1", region="global")])
        self.assertEqual(report.in_state("covered")[0].service, "Amazon Route 53")
        self.assertNotIn("NOT SCANNED", render_billing_coverage(report))

    def test_dns_query_charges_still_read_as_unexplained(self):
        billing = bill(("Amazon Route 53", "", "2.00"))
        billing = BillingResult(
            **{**billing.__dict__,
               "usage_types": (audit.UsageTypeAmount("Amazon Route 53", "DNS-Queries",
                                                     Decimal("1.50")),
                               audit.UsageTypeAmount("Amazon Route 53", "HostedZone",
                                                     Decimal("0.50")))})
        report = audit.assess_billing_coverage(
            billing, coverage_entries=complete_inventory("Route53HostedZone"),
            rows=[make_row("Route53HostedZone", "Z1", region="global")])
        self.assertEqual(report.in_state("partial")[0].unexplained, Decimal("1.50"))

    def test_sub_cent_findings_are_named_not_just_counted(self):
        report = audit.assess_billing_coverage(
            bill(("AWS Glue", "", "0.5"),
                 ("Amazon Athena", "us-east-1", "0.00015"),
                 ("AWS Backup", "us-east-1", "0.000020224")))
        note = below_threshold_note(report)
        self.assertIn("2 further service(s)", note)
        self.assertLess(note.index("Amazon Athena"), note.index("AWS Backup"))
        self.assertIn("0.00015 USD, not scanned", note)
        self.assertNotIn("AWS Glue", note)

    def test_aws_own_breakdown_can_clear_a_partially_covered_bill(self):
        """When AWS's breakdown puts all of "EC2 - Other" on collected types, there is
        no gap finding for this account."""
        billing = bill(("EC2 - Other", "us-east-1", "3.36"))
        billing = BillingResult(
            **{**billing.__dict__,
               "usage_types": (
                   audit.UsageTypeAmount("EC2 - Other", "USE1-EBS:VolumeUsage.gp3",
                                         Decimal("3.30")),
                   audit.UsageTypeAmount("EC2 - Other", "USE1-EBS:SnapshotUsage",
                                         Decimal("0.06")))})
        report = audit.assess_billing_coverage(
            billing, coverage_entries=complete_inventory("EBSVolume", "EBSSnapshot"),
            rows=[make_row("EBSVolume", "vol-1")])
        self.assertEqual(report.in_state("partial"), ())
        entry = report.in_state("covered")[0]
        self.assertEqual(entry.unexplained, Decimal("0"))
        self.assertIn("no sign of inter-AZ and internet data transfer", render_billing_coverage(report))

    def test_nat_gateway_hours_are_explained_now_they_are_scanned(self):
        billing = bill(("EC2 - Other", "us-east-1", "40.00"))
        billing = BillingResult(
            **{**billing.__dict__,
               "usage_types": (
                   audit.UsageTypeAmount("EC2 - Other", "USE1-NatGateway-Hours",
                                         Decimal("31.00")),
                   audit.UsageTypeAmount("EC2 - Other", "USE1-EBS:VolumeUsage.gp3",
                                         Decimal("9.00")))})
        report = audit.assess_billing_coverage(
            billing, coverage_entries=complete_inventory("EBSVolume", "NatGateway"),
            rows=[make_row("EBSVolume", "vol-1"), make_row("NatGateway", "nat-1")])
        self.assertEqual(report.in_state("partial"), ())
        self.assertNotIn("NOT SCANNED", render_billing_coverage(report))

    def test_vpc_endpoint_hours_are_billed_under_amazon_vpc_and_explained(self):
        """Endpoints bill under "Amazon Virtual Private Cloud", not "EC2 - Other"."""
        billing = bill(("Amazon Virtual Private Cloud", "us-east-1", "14.60"))
        billing = BillingResult(
            **{**billing.__dict__,
               "usage_types": (audit.UsageTypeAmount(
                   "Amazon Virtual Private Cloud", "USE1-VpcEndpoint-Hours", Decimal("14.60")),)})
        report = audit.assess_billing_coverage(
            billing, coverage_entries=complete_inventory("VpcEndpoint"),
            rows=[make_row("VpcEndpoint", "vpce-1")])
        self.assertEqual(report.in_state("covered")[0].service, "Amazon Virtual Private Cloud")

    def test_a_data_transfer_line_is_reported_as_unexplained(self):
        """The other half. If the breakdown DOES name something unscanned, the
        finding stands and says how much of the charge it is."""
        billing = bill(("EC2 - Other", "us-east-1", "40.00"))
        billing = BillingResult(
            **{**billing.__dict__,
               "usage_types": (
                   audit.UsageTypeAmount("EC2 - Other", "USE1-DataTransfer-Regional-Bytes",
                                         Decimal("31.00")),
                   audit.UsageTypeAmount("EC2 - Other", "USE1-EBS:VolumeUsage.gp3",
                                         Decimal("9.00")))})
        report = audit.assess_billing_coverage(
            billing, coverage_entries=complete_inventory("EBSVolume"),
            rows=[make_row("EBSVolume", "vol-1")])
        entry = report.in_state("partial")[0]
        self.assertEqual(entry.unexplained, Decimal("31.00"))
        text = render_billing_coverage(report)
        self.assertIn("data transfer 31.00 (NOT SCANNED)", text)

    def test_an_unrecognised_usage_type_counts_as_unexplained(self):
        """Conservative on purpose, and it puts the AWS name in front of
        someone who can add it to the table."""
        billing = bill(("EC2 - Other", "us-east-1", "5.00"))
        billing = BillingResult(
            **{**billing.__dict__,
               "usage_types": (audit.UsageTypeAmount(
                   "EC2 - Other", "USE1-SomethingNew", Decimal("5.00")),)})
        report = audit.assess_billing_coverage(
            billing, coverage_entries=complete_inventory("EBSVolume"),
            rows=[make_row("EBSVolume", "vol-1")])
        self.assertIn("USE1-SomethingNew",
                      [label for label, _a, _c in report.in_state("partial")[0].breakdown])

    def test_the_headline_is_what_the_findings_add_up_to(self):
        report = audit.assess_billing_coverage(
            bill(("Amazon Lex", "us-east-1", "12.00"),
                 ("Tax", "", "5.00")))
        self.assertEqual(report.unaccounted, Decimal("12.00"))
        self.assertIn("12.00 USD of that is listed below",
                      render_billing_coverage(report))


class WordingTests(unittest.TestCase):
    """The sentences are the deliverable: a figure with no caveat beside it is
    the thing this whole pass exists to prevent."""

    def test_a_figure_says_what_period_it_covers(self):
        report = audit.assess_billing_coverage(bill((EC2_COMPUTE, "us-east-1", "1")))
        self.assertIn(str(PERIOD_START), render_billing_coverage(report))
        self.assertIn(str(PERIOD_END), render_billing_coverage(report))

    def test_a_service_charged_nothing_is_never_shown_as_a_figure(self):
        """"0.00 USD" under "charged for, but the scan found none" reads as a
        charge. Being charged 0.00 is being charged nothing."""
        report = audit.assess_billing_coverage(
            bill((EC2_COMPUTE, "us-east-1", "0"),
                 ("Amazon Simple Queue Service", "us-east-1", "0"),
                 ("Amazon Lex", "us-east-1", "12.34")))
        self.assertEqual([s.service for s in report.listed_services()],
                         ["Amazon Lex"])
        self.assertNotIn("0.00 USD", render_billing_coverage(report))

    def test_no_column_label_claims_a_month(self):
        self.assertNotIn("month", audit.COST_COLUMN_LABEL.lower())

    def test_unallocated_money_says_which_bucket_it_is_in(self):
        rows = [make_row("EBSVolume", "vol-1")]
        report = attribute_costs(rows, bill(("EC2 - Other", "us-east-1", "40.00")),
                                 coverage_entries=complete_inventory("EBSVolume"))
        text = render_unallocated(report)
        self.assertIn("EC2 - Other in us-east-1", text)
        self.assertIn("40.00", text)


if __name__ == "__main__":
    unittest.main()
