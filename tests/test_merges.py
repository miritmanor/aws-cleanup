"""naming/merges.py: one project merged into another, re-applied on every load, and
split apart again by forgetting the target's name."""

import contextlib
import io
import itertools
import os
import tempfile
import unittest

from aws_resource_audit.naming.effective import (
    remembered_empty_groups,
    resolve_effective_membership,
)
from aws_resource_audit.naming.group_names import load_group_store, save_group_store
from aws_resource_audit.naming.merges import EditRefused, forget_group, merge_projects
from aws_resource_audit.naming.project_list import project_list


def _scanned(resource_id, project_id, name, method="deployment"):
    """A row as the grouping pass leaves it, detected fields included."""
    return {"service": "LambdaFunction", "region": "us-east-1",
            "resource_id": resource_id, "why_grouped": "",
            "project_id": project_id, "_detected_project_id": project_id,
            "project_group": name, "_detected_project_group": name,
            "grouping_method": method, "_detected_grouping_method": method,
            "membership": "assigned"}


class MergeTests(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "groups.json")
        save_group_store(self.path, {"groups": {}})
        self.rows = [_scanned("a1", "deployment:a", "alpha"),
                     _scanned("a2", "deployment:a", "alpha"),
                     _scanned("b1", "deployment:b", "beta"),
                     _scanned("b2", "deployment:b", "beta"),
                     _scanned("c1", "deployment:c", "gamma"),
                     _scanned("c2", "deployment:c", "gamma"),
                     _scanned("t1", "tag:orders", "orders", method="tag"),
                     _scanned("t2", "tag:orders", "orders", method="tag")]
        self.ids = (f"grp-{n:04d}" for n in itertools.count(1))

    def _resolve(self):
        with contextlib.redirect_stdout(io.StringIO()):
            resolve_effective_membership(self.rows, self.path)

    def _pid_of(self, resource_id):
        return next(r["project_id"] for r in self.rows if r["resource_id"] == resource_id)

    def _merge(self, source_rid, target_rid):
        self._resolve()
        store = load_group_store(self.path)
        gid = merge_projects(store, self.rows, self._pid_of(source_rid),
                             self._pid_of(target_rid), lambda: next(self.ids))
        save_group_store(self.path, store)
        self._resolve()
        return gid

    def _forget(self, gid):
        store = load_group_store(self.path)
        forget_group(store, gid)
        save_group_store(self.path, store)
        self._resolve()

    def _labels(self):
        return {r["resource_id"]: r["project_group"] for r in self.rows}

    def test_a_merged_project_is_shown_under_the_targets_name(self):
        gid = self._merge("b1", "a1")
        self.assertEqual({r["project_id"] for r in self.rows[:4]}, {f"group:{gid}"})
        self.assertEqual({r["project_group"] for r in self.rows[:4]}, {"alpha"})

    def test_the_merge_survives_a_fresh_load(self):
        gid = self._merge("b1", "a1")
        self._resolve()
        self._resolve()
        self.assertEqual({r["project_id"] for r in self.rows[:4]}, {f"group:{gid}"})

    def test_the_project_list_shows_one_project_and_what_was_merged_in(self):
        gid = self._merge("b1", "a1")
        listed = {p["project_id"]: p for p in project_list(self.rows, self.path)}
        self.assertNotIn("deployment:b", listed)
        self.assertEqual(listed[f"group:{gid}"]["resource_count"], 4)
        self.assertEqual(listed[f"group:{gid}"]["merged_from"], ["beta"])
        self.assertEqual(len([p for p in listed.values() if p["name"] == "beta"]), 0)

    def test_forgetting_the_target_splits_both_back_to_default_names(self):
        gid = self._merge("b1", "a1")
        self._forget(gid)
        self.assertEqual(load_group_store(self.path)["groups"], {})
        self.assertEqual([self._pid_of(r) for r in ("a1", "b1")],
                         ["deployment:a", "deployment:b"])
        self.assertEqual(self._labels()["b1"], "beta")

    def test_a_named_source_gets_its_own_name_back_after_a_split(self):
        store = load_group_store(self.path)
        store["groups"]["grp-0100"] = {"name": "Beta site", "members": [
            "LambdaFunction:us-east-1:b1", "LambdaFunction:us-east-1:b2"]}
        save_group_store(self.path, store)
        target = self._merge("b1", "a1")
        self.assertEqual(self._labels()["b1"], "alpha")

        self._forget(target)
        self.assertEqual(self._labels()["b1"], "Beta site")
        self.assertEqual(self._pid_of("b1"), "group:grp-0100")

    def test_forgetting_the_last_target_of_a_chain_keeps_the_inner_merge(self):
        inner = self._merge("b1", "a1")
        outer = self._merge("a1", "c1")
        self.assertEqual({self._labels()[r] for r in ("a1", "b1", "c1")}, {"gamma"})

        self._forget(outer)
        self.assertEqual(self._pid_of("b1"), f"group:{inner}")
        self.assertEqual(self._labels()["b1"], "alpha")
        self.assertEqual(self._pid_of("c1"), "deployment:c")

    def test_a_tag_derived_source_is_refused(self):
        with self.assertRaises(EditRefused) as caught:
            self._merge("t1", "a1")
        self.assertIn("AWS Project tag", str(caught.exception))
        self.assertFalse(caught.exception.conflict)

    def test_a_tag_derived_target_is_allowed(self):
        gid = self._merge("a1", "t1")
        self.assertEqual(self._pid_of("a1"), f"group:{gid}")
        self.assertEqual(self._labels()["a1"], "orders")

    def test_merging_a_project_into_itself_is_refused(self):
        with self.assertRaises(EditRefused):
            self._merge("a1", "a2")

    def test_an_absorbed_name_is_not_listed_as_empty(self):
        self._merge("b1", "a1")
        gone = [r for r in self.rows if not r["resource_id"].startswith("b")]
        self.assertEqual(
            [name for _pid, name in remembered_empty_groups(gone, self.path)], [])


if __name__ == "__main__":
    unittest.main()
