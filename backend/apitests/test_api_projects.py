"""GET /api/projects, and naming a tag project."""

import os
import unittest
from . import _REPO  # noqa: F401  (import for the sys.path fixup side effect)
from aws_resource_audit.rows import member_key
from .api_base import ApiTestCase


class ProjectListTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.rows = self.write_snapshot()

    def _projects(self):
        response = self.client.get("/api/projects")
        self.assertEqual(response.status_code, 200)
        return {p["project_id"]: p for p in response.json()}

    def _tagged_key(self):
        return next(member_key(r) for r in self.rows if r["grouping_method"] == "tag")

    def test_every_project_is_listed_once_by_id(self):
        listed = self._projects()
        ids = {r["project_id"] for r in self.client.get("/api/scan").json()["rows"]
               if r["project_id"]}
        self.assertEqual(set(listed), ids)

    def test_a_tag_project_reports_its_source_and_a_seed(self):
        orders = self._projects()["tag:orders"]
        self.assertEqual(orders["sources"], ["AWS tag"])
        self.assertEqual(orders["name_kind"], "default")
        self.assertTrue(orders["seed_key"])

    def test_a_tag_project_can_be_named(self):
        created = self.client.post(
            "/api/groups", json={"name": "Shop", "member_key": self._tagged_key()})
        self.assertEqual(created.status_code, 201)
        listed = self._projects()
        shop = listed[f"group:{created.json()['id']}"]
        self.assertEqual((shop["name"], shop["name_kind"]), ("Shop", "assigned"))
        self.assertEqual(shop["sources"], ["AWS tag"])
        self.assertGreater(shop["resource_count"], 1)

    def test_a_named_tag_project_still_counts_as_tag_derived(self):
        """Hand-picking protection reads the detected method, which naming leaves alone."""
        from app.api.groups import _tag_derived_keys
        key = self._tagged_key()
        self.client.post("/api/groups", json={"name": "Shop", "member_key": key})
        self.assertIn(key, _tag_derived_keys())

    def test_saved_names_are_listed_without_a_scan(self):
        self.client.post("/api/groups", json={"name": "Shop", "member_key": self._tagged_key()})
        os.remove(os.path.join(self.data_dir, "results", "aws_scan.json"))
        listed = self.client.get("/api/projects").json()
        self.assertEqual([p["name"] for p in listed], ["Shop"])


if __name__ == "__main__":
    unittest.main()
