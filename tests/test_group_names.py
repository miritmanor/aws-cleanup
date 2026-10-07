"""The group-name store applied back onto rows. A manual group keeps the detection
explanation for its detected members."""

import json
import os
import tempfile
import unittest

from aws_resource_audit.analyze.project_summary import summarize_projects
from aws_resource_audit.naming.effective import resolve_effective_membership
from aws_resource_audit.naming.group_names import (
    apply_manual_group_overrides,
    apply_assigned_names,
)
from aws_resource_audit.rows import member_key


def _row(resource_id, why_grouped, group="inferred-cluster-1"):
    return {"service": "LambdaFunction", "region": "us-east-1",
            "resource_id": resource_id, "project_group": group,
            "grouping_method": "inferred", "why_grouped": why_grouped}


class ManualOverrideTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "groups.json")

    def _store(self, rows, name="MyBlog"):
        with open(self.path, "w") as f:
            json.dump({"groups": {"grp-0001": {
                "name": name, "manual": True,
                "members": [member_key(r) for r in rows]}}}, f)

    def test_a_detected_reason_survives_the_override(self):
        rows = [_row("api", "shares VPC vpc-1 with LambdaFunction:us-east-1:worker")]
        self._store(rows)
        apply_manual_group_overrides(rows, self.path)

        self.assertEqual(rows[0]["project_group"], "MyBlog")
        self.assertEqual(rows[0]["grouping_method"], "named")
        self.assertEqual(
            rows[0]["why_grouped"],
            "manually grouped | originally: shares VPC vpc-1 with "
            "LambdaFunction:us-east-1:worker")

    def test_a_row_with_no_detected_reason_says_only_manually_grouped(self):
        rows = [_row("orphan", "", group="")]
        self._store(rows)
        apply_manual_group_overrides(rows, self.path)
        self.assertEqual(rows[0]["why_grouped"], "manually grouped")

    def test_re_applying_does_not_nest_the_marker(self):
        """The scan writes overridden rows to the snapshot, so every later read
        re-runs this over its own output."""
        rows = [_row("api", "name contains 'blog' | shares role blog-exec")]
        self._store(rows)
        apply_manual_group_overrides(rows, self.path)
        once = rows[0]["why_grouped"]
        apply_manual_group_overrides(rows, self.path)
        apply_manual_group_overrides(rows, self.path)

        self.assertEqual(rows[0]["why_grouped"], once)
        self.assertEqual(once.count("manually grouped"), 1)
        self.assertIn("name contains 'blog' | shares role blog-exec", once)

    def test_a_member_outside_this_scan_is_skipped(self):
        rows = [_row("api", "shares VPC vpc-1")]
        self._store(rows)
        with open(self.path) as f:
            store = json.load(f)
        store["groups"]["grp-0001"]["members"].append("LambdaFunction:us-east-1:gone")
        with open(self.path, "w") as f:
            json.dump(store, f)

        apply_manual_group_overrides(rows, self.path)
        self.assertEqual(len(rows), 1)


def _scanned(resource_id, project_id, group):
    """A row as the grouping pass leaves it, detected id included."""
    row = _row(resource_id, "", group=group)
    row.update(project_id=project_id, _detected_project_id=project_id,
               membership="assigned" if project_id else "unassigned")
    return row


class SavedGroupIdentityTests(unittest.TestCase):
    """A saved group is ONE project: one id, one bubble."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "groups.json")

    def _store(self, groups):
        with open(self.path, "w") as f:
            json.dump({"groups": groups}, f)

    def test_a_manual_group_spanning_three_detected_projects_is_one_summary(self):
        rows = [_scanned("a", "deployment:x", "bakery-1"),
                _scanned("b", "deployment:y", "bakery-2"),
                _scanned("c", "", "")]
        self._store({"g1": {"name": "bakery", "manual": True,
                            "members": [member_key(r) for r in rows]}})
        resolve_effective_membership(rows, self.path)

        self.assertEqual({r["project_id"] for r in rows}, {"group:g1"})
        named = [p for p in summarize_projects(rows).projects
                 if p.display_name == "bakery"]
        self.assertEqual(len(named), 1)
        self.assertEqual(named[0].resource_count, 3)

    def test_every_fragment_of_a_split_named_group_shares_one_id(self):
        rows = [_scanned("a", "deployment:x", "frag-1"),
                _scanned("b", "deployment:x", "frag-1"),
                _scanned("c", "deployment:y", "frag-2"),
                _scanned("d", "deployment:y", "frag-2")]
        self._store({"g1": {"name": "bakery",
                            "members": [member_key(r) for r in rows]}})
        apply_assigned_names(rows, self.path)
        self.assertEqual({r["project_id"] for r in rows}, {"group:g1"})

    def test_forgetting_a_group_hands_rows_back_to_the_scans_projects(self):
        rows = [_scanned("a", "deployment:x", "bakery-1"),
                _scanned("b", "", "")]
        self._store({"g1": {"name": "bakery", "manual": True,
                            "members": [member_key(r) for r in rows]}})
        resolve_effective_membership(rows, self.path)
        self._store({})
        resolve_effective_membership(rows, self.path)

        self.assertEqual([r["project_id"] for r in rows], ["deployment:x", ""])


if __name__ == "__main__":
    unittest.main()
