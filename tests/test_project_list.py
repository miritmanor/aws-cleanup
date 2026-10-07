"""naming/project_list.py: the Projects tab's list, built in Python."""

import contextlib
import io
import json
import os
import tempfile
import unittest

from . import FROZEN_ACCOUNT  # noqa: F401  (import for the clock/identity pin)
from . import corpus

import aws_resource_audit as audit
from aws_resource_audit.naming.effective import resolve_effective_membership
from aws_resource_audit.naming.project_list import project_list


class ProjectListTests(unittest.TestCase):
    def setUp(self):
        self.rows, _c, _f, _n = corpus.build()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "groups.json")

    def _listed(self, groups):
        with open(self.path, "w") as f:
            json.dump({"groups": groups}, f)
        with contextlib.redirect_stdout(io.StringIO()):
            resolve_effective_membership(self.rows, self.path)
        return {p["project_id"]: p for p in project_list(self.rows, self.path)}

    def test_a_detected_project_has_a_default_name_and_its_source(self):
        listed = {p["project_id"]: p for p in project_list(self.rows, None)}
        orders = listed["tag:orders"]
        self.assertEqual((orders["name"], orders["name_kind"]), ("orders", "default"))
        self.assertEqual(orders["sources"], ["AWS tag"])
        self.assertIsNone(orders["group_id"])
        self.assertTrue(orders["seed_key"])

    def test_a_named_tag_project_keeps_its_source(self):
        seed = next(audit.member_key(r) for r in self.rows if r["project_id"] == "tag:orders")
        listed = self._listed({"g1": {"name": "Shop", "members": [seed]}})
        self.assertNotIn("tag:orders", listed)
        shop = listed["group:g1"]
        self.assertEqual((shop["name"], shop["name_kind"]), ("Shop", "assigned"))
        self.assertEqual(shop["sources"], ["AWS tag"])
        self.assertEqual(shop["not_applied"], "")

    def test_a_saved_name_that_matches_nothing_says_so(self):
        """Live resources, but under 40% of them in any one project."""
        loose = [audit.member_key(r) for r in self.rows if not r.get("project_id")][:5]
        listed = self._listed({"g1": {"name": "Scattered", "members": loose}})
        self.assertEqual(listed["group:g1"]["resource_count"], 0)
        self.assertIn("40%", listed["group:g1"]["not_applied"])

    def test_the_cli_reports_a_saved_name_that_matches_nothing(self):
        loose = [audit.member_key(r) for r in self.rows if not r.get("project_id")][:5]
        with open(self.path, "w") as f:
            json.dump({"groups": {"g1": {"name": "Scattered", "members": loose}}}, f)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            audit.apply_assigned_names(self.rows, self.path)
        self.assertIn('Saved name "Scattered" not applied', out.getvalue())

    def test_a_saved_name_whose_resources_are_gone_is_not_called_unapplied(self):
        listed = self._listed({"g1": {"name": "Gone", "members": ["Nope:us-east-1:x"]}})
        self.assertEqual(listed["group:g1"]["live_members"], 0)
        self.assertEqual(listed["group:g1"]["not_applied"], "")

    def test_hand_picked_membership_is_its_own_source(self):
        keys = [audit.member_key(r) for r in self.rows if not r.get("project_id")][:2]
        listed = self._listed({"g1": {"name": "Picked", "members": keys, "manual": True}})
        self.assertEqual(listed["group:g1"]["sources"], ["hand-picked"])
        self.assertTrue(listed["group:g1"]["manual"])


if __name__ == "__main__":
    unittest.main()
