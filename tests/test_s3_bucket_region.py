"""An S3 bucket is recorded in its own region (decision 0062): links still reach it from
any region, the per-region S3 bill is split across it, and saved names follow it."""

import json
import os
import tempfile
import unittest
from decimal import Decimal

from .cost_helpers import GLOBAL, bill
from .fakes import make_row

import aws_resource_audit as audit
from aws_resource_audit.analyze.cost import attribute_costs
from aws_resource_audit.billing import ESTIMATED
from aws_resource_audit.naming.effective import resolve_effective_membership
from aws_resource_audit.present.cost import cost_columns

S3 = "Amazon Simple Storage Service"


class CrossRegionLinkTests(unittest.TestCase):
    """A bucket name is unique across AWS, so a reference finds it in any region."""

    def _link(self, **scope):
        job = make_row("GlueJob", "etl", region="us-east-1")
        audit.add_edge(job, "landing-zone", "runs script from", "job Command.ScriptLocation",
                       conn_type="gluejob.s3bucket.script", target_service="S3Bucket", **scope)
        bucket = make_row("S3Bucket", "landing-zone", region="eu-west-1")
        audit.resolve_edges([job, bucket])
        return job, bucket

    def test_a_bare_name_reaches_a_bucket_in_another_region(self):
        job, bucket = self._link()
        self.assertIn("S3Bucket:landing-zone", audit.render_connections(job))
        self.assertIn("used by GlueJob:etl", audit.render_connections(bucket))

    def test_an_s3_arn_which_names_no_region_reaches_it_too(self):
        _job, bucket = self._link(target_arn="arn:aws:s3:::landing-zone",
                                  target_account=None, target_region=None)
        self.assertIn("used by GlueJob:etl", audit.render_connections(bucket))

    def test_the_link_still_counts_toward_removal_risk(self):
        _job, linked = self._link()
        alone = make_row("S3Bucket", "landing-zone", region="eu-west-1")
        audit.resolve_edges([alone])
        self.assertNotEqual(linked["risk_if_removed"], alone["risk_if_removed"])


class RegionalSplitTests(unittest.TestCase):
    """Each region's S3 line is split evenly across that region's buckets, and only those."""

    def setUp(self):
        self.rows = [make_row("S3Bucket", "a", region="us-east-1"),
                     make_row("S3Bucket", "b", region="us-east-1"),
                     make_row("S3Bucket", "c", region="eu-west-1")]
        # S3's inventory is recorded once, account-wide: GLOBAL's scope is "global".
        self.report = attribute_costs(
            self.rows, bill((S3, "us-east-1", "10.00"), (S3, "eu-west-1", "3.00"),
                            (S3, "ap-south-1", "1.00")),
            coverage_entries=GLOBAL)

    def test_each_bucket_gets_an_estimated_share_of_its_own_region(self):
        cells = [cost_columns(r)["est_monthly_cost_usd"] for r in self.rows]
        self.assertEqual(cells, ["5.00", "5.00", "3.00"])
        self.assertTrue(all(r["cost"]["state"] == ESTIMATED for r in self.rows))

    def test_a_region_with_charges_and_no_bucket_stays_unallocated_and_says_why(self):
        south = [b for b in self.report.buckets if b.region == "ap-south-1"][0]
        self.assertEqual(south.allocated, Decimal("0"))
        self.assertIn("no S3Bucket was found in ap-south-1", south.explanation)
        self.assertEqual(self.report.unallocated, Decimal("1.00"))
        self.assertTrue(self.report.reconciles())


class SavedNameTests(unittest.TestCase):
    """Saved member keys from when buckets were "global" still find them."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "groups.json")

    def _rows(self, bucket_region):
        rows = [make_row("LambdaFunction", "api"),
                make_row("S3Bucket", "assets", region=bucket_region)]
        for row in rows:
            row["project_id"] = row["_detected_project_id"] = "inferred:1"
        return rows

    def _save(self, members, manual=False):
        with open(self.path, "w") as f:
            json.dump({"groups": {"grp-0001": {"name": "Shop", "manual": manual,
                                               "members": members}}}, f)

    def _members(self):
        with open(self.path) as f:
            return json.load(f)["groups"]["grp-0001"]["members"]

    def test_an_old_global_key_names_the_project_and_is_rewritten(self):
        self._save(["LambdaFunction:us-east-1:api", "S3Bucket:global:assets"])
        rows = self._rows("eu-west-1")
        resolve_effective_membership(rows, self.path)
        self.assertEqual([r["project_group"] for r in rows], ["Shop", "Shop"])
        self.assertEqual(self._members(),
                         ["LambdaFunction:us-east-1:api", "S3Bucket:eu-west-1:assets"])

    def test_a_manual_group_keeps_its_bucket(self):
        self._save(["LambdaFunction:us-east-1:api", "S3Bucket:global:assets"], manual=True)
        rows = self._rows("eu-west-1")
        resolve_effective_membership(rows, self.path)
        self.assertEqual(rows[1]["project_group"], "Shop")

    def test_an_old_snapshot_still_finds_a_new_key(self):
        self._save(["LambdaFunction:us-east-1:api", "S3Bucket:eu-west-1:assets"], manual=True)
        rows = self._rows("global")
        resolve_effective_membership(rows, self.path)
        self.assertEqual(rows[1]["project_group"], "Shop")


if __name__ == "__main__":
    unittest.main()
