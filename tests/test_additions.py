"""naming/additions.py: hand-picked resources added to a project. A project that grows
on its own keeps growing."""

import contextlib
import io
import itertools
import os
import tempfile
import unittest

from aws_resource_audit.naming.additions import add_to_project
from aws_resource_audit.naming.effective import resolve_effective_membership
from aws_resource_audit.naming.group_names import load_group_store, save_group_store
from aws_resource_audit.naming.merges import EditRefused, forget_group
from aws_resource_audit.naming.project_list import project_list
from aws_resource_audit.rows import member_key

from .test_merges import _scanned


class AddToProjectTests(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "groups.json")
        save_group_store(self.path, {"groups": {}})
        self.rows = [_scanned("a1", "deployment:a", "alpha"),
                     _scanned("a2", "deployment:a", "alpha"),
                     _scanned("bucket", "", ""),
                     _scanned("t1", "tag:orders", "orders", method="tag")]
        self.ids = (f"grp-{n:04d}" for n in itertools.count(1))

    def _resolve(self):
        with contextlib.redirect_stdout(io.StringIO()):
            resolve_effective_membership(self.rows, self.path)

    def _row(self, resource_id):
        return next(r for r in self.rows if r["resource_id"] == resource_id)

    def _add(self, project_id, *resource_ids):
        self._resolve()
        store = load_group_store(self.path)
        keys = [member_key(self._row(r)) for r in resource_ids]
        gid = add_to_project(store, self.rows, project_id, keys, lambda: next(self.ids))
        save_group_store(self.path, store)
        self._resolve()
        return gid

    def test_a_default_named_project_is_saved_under_its_name_and_takes_the_pick(self):
        gid = self._add("deployment:a", "bucket")
        self.assertEqual(self._row("bucket")["project_id"], f"group:{gid}")
        self.assertEqual(self._row("bucket")["project_group"], "alpha")
        group = load_group_store(self.path)["groups"][gid]
        self.assertFalse(group.get("manual"))
        self.assertEqual(group["extra_members"], ["LambdaFunction:us-east-1:bucket"])

    def test_the_project_keeps_growing_after_an_addition(self):
        gid = self._add("deployment:a", "bucket")
        self.rows.append(_scanned("a3", "deployment:a", "alpha"))
        self._resolve()
        self.assertEqual(self._row("a3")["project_id"], f"group:{gid}")

    def test_a_manual_group_lists_the_pick_as_a_member(self):
        save_group_store(self.path, {"groups": {"grp-0100": {
            "name": "Picked", "manual": True,
            "members": [member_key(self._row("a1")), member_key(self._row("a2"))]}}})
        self._add("group:grp-0100", "bucket")
        group = load_group_store(self.path)["groups"]["grp-0100"]
        self.assertIn(member_key(self._row("bucket")), group["members"])
        self.assertNotIn("extra_members", group)

    def test_a_pick_listed_by_another_saved_name_is_a_conflict(self):
        save_group_store(self.path, {"groups": {"grp-0100": {
            "name": "Other", "manual": True,
            "members": [member_key(self._row("bucket")), member_key(self._row("t1"))]}}})
        with self.assertRaises(EditRefused) as caught:
            self._add("deployment:a", "bucket")
        self.assertTrue(caught.exception.conflict)

    def test_a_tag_derived_pick_is_refused(self):
        with self.assertRaises(EditRefused) as caught:
            self._add("deployment:a", "t1")
        self.assertIn("AWS Project tag", str(caught.exception))

    def test_a_tag_project_can_take_a_pick(self):
        gid = self._add("tag:orders", "bucket")
        self.assertEqual(self._row("bucket")["project_id"], f"group:{gid}")
        self.assertEqual(self._row("bucket")["project_group"], "orders")

    def test_forgetting_the_name_lets_the_pick_go(self):
        gid = self._add("deployment:a", "bucket")
        store = load_group_store(self.path)
        forget_group(store, gid)
        save_group_store(self.path, store)
        self._resolve()
        self.assertEqual(self._row("bucket")["project_id"], "")
        self.assertEqual(self._row("a1")["project_id"], "deployment:a")

    def test_the_project_list_counts_the_picks(self):
        gid = self._add("deployment:a", "bucket")
        listed = {p["project_id"]: p for p in project_list(self.rows, self.path)}
        self.assertEqual(listed[f"group:{gid}"]["extra_members"], 1)
        self.assertEqual(listed[f"group:{gid}"]["resource_count"], 3)


if __name__ == "__main__":
    unittest.main()
