"""Naming groups and manual grouping over HTTP: each takes effect without a scan."""

import unittest
from . import _REPO  # noqa: F401  (import for the sys.path fixup side effect)
from aws_resource_audit.rows import member_key
from .api_base import ApiTestCase


class GroupNamingTests(ApiTestCase):
    """Naming without re-scanning."""

    def setUp(self):
        super().setUp()
        self.rows = self.write_snapshot()

    def _a_project_member(self):
        """A member key from a row in a project, and that project's name."""
        body = self.client.get("/api/scan").json()
        for row, source in zip(body["rows"], self.rows):
            if row["project_id"]:
                return member_key(source), row["project_group"]
        self.fail("the corpus has no project to name")

    def test_naming_a_project_takes_effect_without_a_scan(self):
        key, label = self._a_project_member()
        response = self.client.post(
            "/api/groups", json={"name": "MyBlog", "member_key": key})
        self.assertEqual(response.status_code, 201)

        rows = self.client.get("/api/scan").json()["rows"]
        named = [r for r in rows if r["project_group"] == "MyBlog"]
        self.assertTrue(named, "the name did not reach the rows")
        self.assertTrue(all(r["grouping_method"] == "named" for r in named))
        # The whole project inherited it, not just the seeded member.
        self.assertEqual(len(named),
                         len([r for r in rows if r["project_group"] == "MyBlog"]))
        self.assertFalse([r for r in rows if r["project_group"] == label])

    def test_renaming_takes_effect_too(self):
        """Snapshot rows already carry grouping_method 'named'; store.py must reset it
        before re-applying, or a rename never takes effect."""
        key, _label = self._a_project_member()
        created = self.client.post(
            "/api/groups", json={"name": "MyBlog", "member_key": key}).json()

        response = self.client.put(f"/api/groups/{created['id']}",
                                   json={"name": "Renamed"})
        self.assertEqual(response.status_code, 200)

        rows = self.client.get("/api/scan").json()["rows"]
        self.assertTrue([r for r in rows if r["project_group"] == "Renamed"])
        self.assertFalse([r for r in rows if r["project_group"] == "MyBlog"])

    def test_a_member_already_named_is_refused(self):
        """Silently moving a resource between groups would rewrite a name the
        user set earlier, from a form that said nothing about it."""
        key, _ = self._a_project_member()
        self.client.post("/api/groups", json={"name": "MyBlog", "member_key": key})
        response = self.client.post(
            "/api/groups", json={"name": "Other", "member_key": key})
        self.assertEqual(response.status_code, 409)

    def test_a_member_key_not_in_the_scan_is_refused(self):
        response = self.client.post(
            "/api/groups",
            json={"name": "Nope", "member_key": "Lambda:us-east-1:not-real"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("not in the current scan", response.json()["detail"])

    def test_deleting_a_name_reverts_the_rows(self):
        key, label = self._a_project_member()
        created = self.client.post(
            "/api/groups", json={"name": "MyBlog", "member_key": key}).json()
        self.assertEqual(
            self.client.delete(f"/api/groups/{created['id']}").status_code, 204)

        rows = self.client.get("/api/scan").json()["rows"]
        self.assertFalse([r for r in rows if r["project_group"] == "MyBlog"])
        self.assertTrue([r for r in rows if r["project_group"] == label])

    def test_two_groups_coexist_and_renaming_one_leaves_the_other(self):
        """Renaming one group must not take another group's rows: each snapshot read is
        a fresh contest between every saved name and every project."""
        projects, seen = [], set()
        body = self.client.get("/api/scan").json()
        for row, source in zip(body["rows"], self.rows):
            if row["project_id"] and row["project_id"] not in seen:
                seen.add(row["project_id"])
                projects.append((member_key(source), row["project_group"]))
            if len(projects) == 2:
                break
        if len(projects) < 2:
            self.skipTest("the corpus has fewer than two projects")

        first = self.client.post(
            "/api/groups",
            json={"name": "Alpha", "member_key": projects[0][0]}).json()
        self.client.post(
            "/api/groups", json={"name": "Beta", "member_key": projects[1][0]})

        rows = self.client.get("/api/scan").json()["rows"]
        alpha = len([r for r in rows if r["project_group"] == "Alpha"])
        beta = len([r for r in rows if r["project_group"] == "Beta"])
        self.assertGreater(alpha, 0)
        self.assertGreater(beta, 0)

        self.client.put(f"/api/groups/{first['id']}", json={"name": "Gamma"})
        rows = self.client.get("/api/scan").json()["rows"]
        self.assertEqual(len([r for r in rows if r["project_group"] == "Gamma"]), alpha)
        self.assertEqual(len([r for r in rows if r["project_group"] == "Beta"]), beta)
        self.assertFalse([r for r in rows if r["project_group"] == "Alpha"])

    def test_group_ids_do_not_get_reused(self):
        """A freed number attached to a new group would silently rebind
        whatever a stale client still had open under that id."""
        key, _ = self._a_project_member()
        first = self.client.post(
            "/api/groups", json={"name": "One", "member_key": key}).json()
        self.assertEqual(first["id"], "grp-0001")
        self.client.delete(f"/api/groups/{first['id']}")
        second = self.client.post(
            "/api/groups", json={"name": "Two", "member_key": key}).json()
        self.assertNotEqual(second["id"], first["id"])

    def test_a_new_name_covers_the_whole_project_not_just_the_seed(self):
        """The seed names the project. The extended membership is written on the next
        snapshot read, so an earlier response reports one member."""
        key, label = self._a_project_member()
        rows = self.client.get("/api/scan").json()["rows"]
        project_size = len([r for r in rows if r["project_group"] == label])
        self.assertGreater(project_size, 1, "need a project bigger than its seed")

        created = self.client.post(
            "/api/groups", json={"name": "MyBlog", "member_key": key}).json()
        self.assertEqual(created["live_members"], project_size)

        listed = self.client.get("/api/groups").json()[0]
        self.assertEqual(listed["live_members"], project_size)
        self.assertEqual(len(listed["members"]), project_size)

    def test_a_group_reports_how_much_of_it_is_still_present(self):
        key, _ = self._a_project_member()
        self.client.post("/api/groups", json={"name": "MyBlog", "member_key": key})
        group = self.client.get("/api/groups").json()[0]
        self.assertEqual(group["name"], "MyBlog")
        self.assertGreater(group["live_members"], 0)
        self.assertTrue(all(m["present"] for m in group["members"]))


class ManualGroupingTests(ApiTestCase):
    """Forcing a multi-select into one group across project boundaries."""

    def setUp(self):
        super().setUp()
        self.rows = self.write_snapshot()

    def _two_unrelated_members(self):
        """Two member keys in different projects, excluding tag-derived rows (reserved
        for the tag-protection test)."""
        body = self.client.get("/api/scan").json()
        by_label = {}
        for row, source in zip(body["rows"], self.rows):
            if row["grouping_method"] == "tag":
                continue
            label = row["project_group"] or None
            key = member_key(source)
            by_label.setdefault(label, key)   # first member seen per label
        keys = list(by_label.values())
        if len(keys) < 2:
            self.fail("the corpus needs at least two differently-grouped, non-tag resources")
        return keys[0], keys[1]

    def _a_tagged_member(self):
        body = self.client.get("/api/scan").json()
        for row, source in zip(body["rows"], self.rows):
            if row["grouping_method"] == "tag":
                return member_key(source)
        self.fail("the corpus has no tag-derived resource")

    def _why_grouped(self):
        return {member_key(source): row["why_grouped"]
                for row, source in zip(self.client.get("/api/scan").json()["rows"], self.rows)}

    def test_grouping_two_unrelated_resources_merges_them(self):
        a, b = self._two_unrelated_members()
        detected = self._why_grouped()
        response = self.client.post(
            "/api/groups/manual", json={"name": "Merged", "member_keys": [a, b]})
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["manual"])
        self.assertEqual({m["member_key"] for m in body["members"]}, {a, b})

        rows = {member_key(source): row
                for row, source in zip(self.client.get("/api/scan").json()["rows"], self.rows)}
        self.assertEqual(rows[a]["project_group"], "Merged")
        self.assertEqual(rows[b]["project_group"], "Merged")
        self.assertEqual(rows[a]["grouping_method"], "named")
        self.assertEqual(rows[b]["grouping_method"], "named")
        # The marker leads; whatever detection had recorded stays behind it.
        for key in (a, b):
            self.assertTrue(rows[key]["why_grouped"].startswith("manually grouped"))
            if detected[key]:
                self.assertIn(detected[key], rows[key]["why_grouped"])

    def test_a_single_member_key_is_refused(self):
        """One key is the seed endpoint's job; the schema rejects it here (422)."""
        a, _b = self._two_unrelated_members()
        response = self.client.post(
            "/api/groups/manual", json={"name": "Solo", "member_keys": [a]})
        self.assertEqual(response.status_code, 422)

    def test_a_member_already_in_a_group_is_refused(self):
        a, b = self._two_unrelated_members()
        self.client.post("/api/groups", json={"name": "Existing", "member_key": a})
        response = self.client.post(
            "/api/groups/manual", json={"name": "Merged", "member_keys": [a, b]})
        self.assertEqual(response.status_code, 409)
        self.assertIn("Existing", response.json()["detail"])

    def test_a_key_outside_the_scan_is_refused(self):
        a, _b = self._two_unrelated_members()
        response = self.client.post(
            "/api/groups/manual",
            json={"name": "Merged", "member_keys": [a, "Lambda:us-east-1:not-real"]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("not in the current scan", response.json()["detail"])

    def test_a_tag_derived_member_cannot_be_manually_regrouped(self):
        """A project tag states membership, so hand-picking a tagged resource
        into another project is refused. Naming is allowed."""
        tagged = self._a_tagged_member()
        a, _b = self._two_unrelated_members()
        other = a if a != tagged else _b
        response = self.client.post(
            "/api/groups/manual", json={"name": "Merged", "member_keys": [tagged, other]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Project tag", response.json()["detail"])

    def test_adding_a_member_to_an_existing_group_makes_it_manual(self):
        a, b = self._two_unrelated_members()
        created = self.client.post(
            "/api/groups", json={"name": "MyBlog", "member_key": a}).json()
        self.assertFalse(created["manual"])

        response = self.client.post(
            f"/api/groups/{created['id']}/members", json={"member_keys": [b]})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["manual"])
        self.assertIn(b, {m["member_key"] for m in body["members"]})

        rows = {member_key(source): row
                for row, source in zip(self.client.get("/api/scan").json()["rows"], self.rows)}
        self.assertEqual(rows[b]["project_group"], "MyBlog")

    def test_adding_to_an_unknown_group_is_a_404(self):
        response = self.client.post(
            "/api/groups/grp-9999/members", json={"member_keys": ["Lambda:us-east-1:x"]})
        self.assertEqual(response.status_code, 404)

    def test_forgetting_a_manual_group_reverts_the_rows(self):
        a, b = self._two_unrelated_members()
        created = self.client.post(
            "/api/groups/manual", json={"name": "Merged", "member_keys": [a, b]}).json()
        self.assertEqual(
            self.client.delete(f"/api/groups/{created['id']}").status_code, 204)

        rows = {member_key(source): row
                for row, source in zip(self.client.get("/api/scan").json()["rows"], self.rows)}
        self.assertNotEqual(rows[a]["project_group"], "Merged")
        self.assertNotEqual(rows[b]["project_group"], "Merged")


if __name__ == "__main__":
    unittest.main()
