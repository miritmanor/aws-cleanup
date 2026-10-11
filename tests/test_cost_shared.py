"""A shared, undivided bill says which bill, how much and who shares it, and its reason
holds up against AWS's own usage-type split."""

import unittest
from decimal import Decimal

from .cost_helpers import attributed, bill, complete_inventory, with_usage
from .fakes import make_row

from aws_resource_audit.analyze.cost import attribute_costs
from aws_resource_audit.billing import (
    SHARED_UNALLOCATED, CostAttribution, restore_attribution,
)
from aws_resource_audit.present.cost import cost_basis, cost_columns

EC2_OTHER = "EC2 - Other"
EC2_OTHER_TYPES = complete_inventory("EBSVolume", "EBSSnapshot", "ElasticIP",
                                     "AMI", "NatGateway")


def ami_account():
    """Three AMIs and their four snapshots, billed only for snapshot storage."""
    rows = ([make_row("AMI", f"ami-{n}") for n in range(3)]
            + [make_row("EBSSnapshot", f"snap-{n}") for n in range(4)])
    billing = with_usage(bill((EC2_OTHER, "us-east-1", "2.47")),
                         (EC2_OTHER, "EBS:SnapshotUsage", "2.47"))
    return rows, billing


class SharedBillTests(unittest.TestCase):

    def test_the_cost_basis_names_the_bill_its_total_and_its_size(self):
        rows, billing = ami_account()
        cells = attributed(rows, billing, EC2_OTHER_TYPES)
        self.assertEqual(cells[0]["cost"],
                         "shared 'EC2 - Other' in us-east-1: 2.47 USD, 7 resources")
        self.assertEqual(cells[0]["est_monthly_cost_usd"], "")

    def test_the_notes_say_what_the_bill_is_made_of_and_who_shares_it(self):
        rows, billing = ami_account()
        notes = attributed(rows, billing, EC2_OTHER_TYPES)[0]["cost_notes"]
        self.assertEqual(notes, "2.47 USD under 'EC2 - Other' in us-east-1. "
                                "Account-wide split: EBS snapshot storage 2.47. "
                                "Shared by 3 AMI, 4 EBSSnapshot.")

    def test_a_disproven_gap_is_not_given_as_the_reason(self):
        """AWS's split shows no data transfer, so the reason must not claim there is some."""
        rows, billing = ami_account()
        report = attribute_costs(rows, billing, coverage_entries=EC2_OTHER_TYPES)
        why = report.buckets[0].explanation
        self.assertNotIn("this bill also covers", why)
        self.assertIn("AWS's own split of it (EBS snapshot storage) names no resource",
                      why)

    def test_a_disproven_gap_still_leaves_the_money_undivided(self):
        rows, billing = ami_account()
        report = attribute_costs(rows, billing, coverage_entries=EC2_OTHER_TYPES)
        self.assertEqual(report.allocated, Decimal("0"))
        self.assertTrue(report.reconciles())

    def test_real_data_transfer_keeps_the_gap_reason(self):
        rows, _ = ami_account()
        billing = with_usage(bill((EC2_OTHER, "us-east-1", "3.00")),
                             (EC2_OTHER, "EBS:SnapshotUsage", "2.47"),
                             (EC2_OTHER, "DataTransfer-Regional-Bytes", "0.53"))
        report = attribute_costs(rows, billing, coverage_entries=EC2_OTHER_TYPES)
        self.assertIn("this bill also covers", report.buckets[0].explanation)

    def test_the_shared_fields_survive_the_snapshot(self):
        rows, billing = ami_account()
        attribute_costs(rows, billing, coverage_entries=EC2_OTHER_TYPES)
        restored = restore_attribution(rows[0]["cost"])
        self.assertEqual(restored.shared_amount, Decimal("2.47"))
        self.assertEqual(restored.shared_count, 7)

    def test_an_attribution_without_the_shared_fields_keeps_the_old_word(self):
        """A shared row with no total (an older snapshot, a placement refusal)."""
        self.assertEqual(cost_basis(CostAttribution(state=SHARED_UNALLOCATED)),
                         "unallocated (shared bill)")
        old = {"state": SHARED_UNALLOCATED, "bucket": "'EC2 - Other' in us-east-1"}
        self.assertEqual(cost_columns({"cost": old})["cost"], "unallocated (shared bill)")


if __name__ == "__main__":
    unittest.main()
