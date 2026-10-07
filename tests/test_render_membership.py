"""Renaming a project needs no AWS scan: render_outputs() and SnapshotStore both apply
the saved names via resolve_effective_membership."""

import contextlib
import io
import json
import os
import re
import tempfile
import unittest

from . import FROZEN_ACCOUNT  # noqa: F401  (import for the clock/identity pin)
from . import corpus
from .fakes import make_row

import aws_resource_audit as audit
from aws_resource_audit.naming.effective import (
    remembered_empty_groups,
    resolve_effective_membership,
)


def _project_view(payload):
    """The "By project" view of an embedded overview payload."""
    return next(v for v in payload["views"] if v["id"] == "project")


def inferred_cluster(label="inferred-cluster-1", project_id="dep:stack9001", n=3):
    """Rows forming ONE detected project, with the _detected_* fields the
    grouping pass would have written."""
    rows = []
    for i in range(n):
        row = make_row("LambdaFunction", f"fn{i}", f"fn{i}")
        row["project_id"] = project_id
        row["project_group"] = label
        row["grouping_method"] = "inferred"
        row["membership"] = "assigned"
        row["why_grouped"] = "same CloudFormation stack"
        row["_detected_project_id"] = project_id
        row["_detected_project_group"] = label
        row["_detected_grouping_method"] = "inferred"
        rows.append(row)
    return rows


class RenderAppliesTheGroupStoreTests(unittest.TestCase):
    def _rendered(self, tmp, rows, groups=None):
        """Write a snapshot (and optionally a group store), then render."""
        settings = audit.Settings(output_dir=tmp)
        paths = audit.output_paths(settings)
        audit.write_snapshot(paths["snapshot"], rows, {"nodes": [], "edges": []}, {},
                             account=FROZEN_ACCOUNT, regions=["us-east-1"],
                             scanned_at=audit.now())
        if groups is not None:
            with open(paths["groups"], "w") as f:
                json.dump(groups, f)
        snapshot = audit.read_snapshot(paths["snapshot"])
        with contextlib.redirect_stdout(io.StringIO()):
            audit.render_outputs(snapshot, paths, settings)
        with open(paths["html"]) as f:
            return f.read(), paths

    def test_a_rename_in_the_group_store_reaches_the_regenerated_report(self):
        """The bug, pinned. No scan involved: render_outputs() reads the store
        itself, so naming a group is a re-render and never a re-scan."""
        rows = inferred_cluster()
        store = {"groups": {"grp-0001": {"name": "RenamedByHand",
                                         "members": [audit.member_key(rows[0])]}}}
        with tempfile.TemporaryDirectory() as tmp:
            html, _paths = self._rendered(tmp, rows, store)
        self.assertIn("RenamedByHand", html)
        self.assertNotIn("inferred-cluster-1", html)

    def test_the_overview_uses_the_renamed_label_too(self):
        """The bubble chart and the table have to agree about what a project is
        called, which they only do if both read the same resolved rows."""
        rows = inferred_cluster()
        store = {"groups": {"grp-0001": {"name": "RenamedByHand",
                                         "members": [audit.member_key(rows[0])]}}}
        with tempfile.TemporaryDirectory() as tmp:
            html, _paths = self._rendered(tmp, rows, store)
        payload = json.loads(
            re.search(r"const BUBBLES = (\{.*?\});\n", html, re.S).group(1))
        names = {p["display_name"] for p in _project_view(payload)["items"]}
        self.assertIn("RenamedByHand", names)

    def test_one_rename_claims_the_whole_cluster(self):
        """One stored member names its whole cluster: one project of three."""
        rows = inferred_cluster(n=3)
        store = {"groups": {"grp-0001": {"name": "Claimed",
                                         "members": [audit.member_key(rows[0])]}}}
        with tempfile.TemporaryDirectory() as tmp:
            html, _paths = self._rendered(tmp, rows, store)
        payload = json.loads(
            re.search(r"const BUBBLES = (\{.*?\});\n", html, re.S).group(1))
        claimed = [p for p in _project_view(payload)["items"] if p["display_name"] == "Claimed"]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["resource_count"], 3)

    def test_rendering_without_a_group_store_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            html, _paths = self._rendered(tmp, inferred_cluster(), None)
        self.assertIn("const BUBBLES = {", html)
        self.assertIn("inferred-cluster-1", html)

    def test_an_empty_remembered_group_reaches_the_overview_with_no_circle(self):
        """A group whose last resource is gone keeps its name and loses its
        bubble - render_outputs has to pass remembered_empty_groups through."""
        rows = inferred_cluster()
        store = {"groups": {"grp-0009": {"name": "AllGone",
                                         "members": ["LambdaFunction:us-east-1:deleted"]}}}
        with tempfile.TemporaryDirectory() as tmp:
            html, _paths = self._rendered(tmp, rows, store)
        payload = json.loads(
            re.search(r"const BUBBLES = (\{.*?\});\n", html, re.S).group(1))
        gone = [p for p in _project_view(payload)["items"] if p["display_name"] == "AllGone"]
        self.assertEqual(len(gone), 1)
        self.assertEqual(gone[0]["resource_count"], 0)
        self.assertIsNone(gone[0]["unused_pct"])
        drawn = {b["id"] for b in _project_view(payload)["packs"]["resource_count"]}
        self.assertNotIn("group:grp-0009", drawn)

    def test_no_aws_call_is_made_while_rendering(self):
        """The renderer module has no boto3."""
        import aws_resource_audit.render as render_module
        self.assertNotIn("boto3", dir(render_module))


class ResolveEffectiveMembershipTests(unittest.TestCase):
    def test_a_rename_takes_effect_on_rows_already_named_by_a_scan(self):
        """reset_assigned_names runs first, or a rename never takes hold."""
        rows, _c, _f, _n = corpus.build()
        seed = next(audit.member_key(r) for r in rows if r.get("project_id"))
        for row in rows:
            if audit.member_key(row) == seed:
                row["grouping_method"] = "named"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "groups.json")
            with open(path, "w") as f:
                json.dump({"groups": {"g1": {"name": "Reclaimed", "members": [seed]}}}, f)
            with contextlib.redirect_stdout(io.StringIO()):
                resolve_effective_membership(rows, path)
        renamed = [r for r in rows if r["project_group"] == "Reclaimed"]
        self.assertTrue(renamed)

    def test_it_is_safe_with_no_group_file(self):
        rows, _c, _f, _n = corpus.build()
        resolve_effective_membership(rows, None)   # must not raise
        self.assertTrue(rows)

    def _named(self, rows, seed, name="Whatever"):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "groups.json")
            with open(path, "w") as f:
                json.dump({"groups": {"g1": {"name": name, "members": [seed]}}}, f)
            with contextlib.redirect_stdout(io.StringIO()):
                resolve_effective_membership(rows, path)

    def test_a_tag_project_can_be_named(self):
        """Every project can be named, whatever its source."""
        rows, _c, _f, _n = corpus.build()
        tagged = [r for r in rows if r.get("project_id") == "tag:orders"]
        self._named(rows, audit.member_key(tagged[0]))
        self.assertTrue(all(r["project_group"] == "Whatever" for r in tagged))
        self.assertTrue(all(r["project_id"] == "group:g1" for r in tagged))

    def test_forgetting_a_name_restores_what_was_detected(self):
        """The default name and the per-resource grouping method come back,
        not just the ID - a tagged row reads "tag" again, not "inferred"."""
        rows, _c, _f, _n = corpus.build()
        before = {audit.member_key(r): (r["project_id"], r["project_group"],
                                        r["grouping_method"]) for r in rows}
        seed = next(audit.member_key(r) for r in rows if r.get("project_id") == "tag:orders")
        self._named(rows, seed)
        resolve_effective_membership(rows, None)
        after = {audit.member_key(r): (r["project_id"], r["project_group"],
                                       r["grouping_method"]) for r in rows}
        self.assertEqual(before, after)

    def test_two_projects_with_one_name_are_not_claimed_together(self):
        """Matching keys on project_id: a name shared by two projects must not
        let a saved name claim both."""
        rows = inferred_cluster("same", "dep:a", 2) + inferred_cluster("same", "dep:b", 2)
        for i, row in enumerate(rows[2:]):
            row["resource_id"] = row["name"] = f"other{i}"
        self._named(rows, audit.member_key(rows[0]))
        self.assertEqual([r["project_id"] for r in rows],
                         ["group:g1", "group:g1", "dep:b", "dep:b"])


class RememberedEmptyGroupsTests(unittest.TestCase):
    def test_a_group_whose_members_are_all_gone_is_reported(self):
        rows, _c, _f, _n = corpus.build()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "groups.json")
            with open(path, "w") as f:
                json.dump({"groups": {"g1": {"name": "Deleted",
                                             "members": ["Nope:us-east-1:gone"]}}}, f)
            self.assertEqual(remembered_empty_groups(rows, path), (("group:g1", "Deleted"),))

    def test_a_group_with_one_surviving_member_is_not_reported(self):
        rows, _c, _f, _n = corpus.build()
        seed = audit.member_key(rows[0])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "groups.json")
            with open(path, "w") as f:
                json.dump({"groups": {"g1": {"name": "Alive",
                                             "members": [seed, "Nope:us-east-1:gone"]}}}, f)
            self.assertEqual(remembered_empty_groups(rows, path), ())

    def test_no_group_file_means_nothing_remembered(self):
        self.assertEqual(remembered_empty_groups([], None), ())


if __name__ == "__main__":
    unittest.main()
