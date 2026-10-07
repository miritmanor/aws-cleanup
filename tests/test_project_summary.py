"""The overview's per-project numbers: counted on project_id (never the label) and on
usage_state values (never flag wording)."""

import unittest

from . import FROZEN_ACCOUNT  # noqa: F401  (import for the clock/identity pin)
from .fakes import make_row

import aws_resource_audit as audit
from aws_resource_audit.analyze.project_summary import (
    KIND_PROJECT,
    KIND_SHARED,
    KIND_UNASSIGNED,
    summarize_projects,
)
from aws_resource_audit.staleness import USAGE_ACTIVE, USAGE_UNKNOWN, USAGE_UNUSED


def row(service="LambdaFunction", rid="fn", *, project_id="", name="",
        usage=USAGE_ACTIVE, membership="assigned"):
    """One row with its project and usage verdict set directly."""
    r = make_row(service, rid)
    r["project_id"] = project_id
    r["project_group"] = name
    r["membership"] = membership if project_id else membership
    r["usage_state"] = usage
    return r


class CountsReconcileTests(unittest.TestCase):
    def test_every_row_lands_in_exactly_one_summary(self):
        """The whole point of the inventory buckets. A shared resource counted
        in each project that uses it would inflate every one of them."""
        rows = [
            row(rid="a", project_id="p1", name="Payments"),
            row(rid="b", project_id="p1", name="Payments"),
            row(rid="c", membership="shared"),
            row(rid="d", membership="unassigned"),
        ]
        overview = summarize_projects(rows)
        self.assertEqual(sum(p.resource_count for p in overview.projects), len(rows))
        kinds = {p.kind for p in overview.projects}
        self.assertEqual(kinds, {KIND_PROJECT, KIND_SHARED, KIND_UNASSIGNED})

    def test_a_shared_resource_is_counted_once(self):
        rows = [
            row(rid="a", project_id="p1", name="Payments"),
            row(rid="shared", membership="shared"),
        ]
        overview = summarize_projects(rows)
        by_kind = {p.kind: p for p in overview.projects}
        self.assertEqual(by_kind[KIND_PROJECT].resource_count, 1)
        self.assertEqual(by_kind[KIND_SHARED].resource_count, 1)


class IdentityTests(unittest.TestCase):
    """project_id is the identity. The label is neither unique nor stable."""

    def test_two_projects_sharing_a_display_name_stay_separate(self):
        rows = [
            row(rid="a", project_id="p1", name="api"),
            row(rid="b", project_id="p2", name="api"),
        ]
        overview = summarize_projects(rows)
        self.assertEqual(len(overview.projects), 2)
        self.assertEqual({p.project_id for p in overview.projects}, {"p1", "p2"})

    def test_renaming_does_not_split_a_project(self):
        """Two rows of one project whose labels disagree - which is what a
        half-applied rename looks like - are still ONE project."""
        rows = [
            row(rid="a", project_id="p1", name="old-name"),
            row(rid="b", project_id="p1", name="new-name"),
        ]
        overview = summarize_projects(rows)
        self.assertEqual(len(overview.projects), 1)
        self.assertEqual(overview.projects[0].resource_count, 2)

    def test_a_project_with_no_label_falls_back_to_its_id(self):
        overview = summarize_projects([row(rid="a", project_id="p1", name="")])
        self.assertEqual(overview.projects[0].display_name, "p1")


class UsageTests(unittest.TestCase):
    def test_unknown_never_becomes_unused(self):
        """Missing telemetry is not idleness: an all-unknown project is not "0% unused"."""
        rows = [row(rid=str(i), project_id="p1", name="p", usage=USAGE_UNKNOWN)
                for i in range(5)]
        summary = summarize_projects(rows).projects[0]
        self.assertEqual(summary.unused, 0)
        self.assertEqual(summary.unknown, 5)
        self.assertEqual(summary.unused_pct, 0.0)
        self.assertTrue(summary.all_unknown)

    def test_unknown_stays_in_the_percentage_denominator(self):
        rows = [
            row(rid="a", project_id="p1", name="p", usage=USAGE_UNUSED),
            row(rid="b", project_id="p1", name="p", usage=USAGE_ACTIVE),
            row(rid="c", project_id="p1", name="p", usage=USAGE_UNKNOWN),
            row(rid="d", project_id="p1", name="p", usage=USAGE_UNKNOWN),
        ]
        summary = summarize_projects(rows).projects[0]
        # 1 of 4, not 1 of 2 - the denominator is every current resource.
        self.assertEqual(summary.unused_pct, 25.0)
        self.assertEqual((summary.unused, summary.active, summary.unknown), (1, 1, 2))

    def test_counts_sum_to_the_resource_count(self):
        rows = [row(rid="a", project_id="p1", usage=USAGE_UNUSED),
                row(rid="b", project_id="p1", usage=USAGE_ACTIVE),
                row(rid="c", project_id="p1", usage=USAGE_UNKNOWN)]
        s = summarize_projects(rows).projects[0]
        self.assertEqual(s.unused + s.active + s.unknown, s.resource_count)

    def test_a_partly_known_project_is_not_all_unknown(self):
        rows = [row(rid="a", project_id="p1", usage=USAGE_UNKNOWN),
                row(rid="b", project_id="p1", usage=USAGE_ACTIVE)]
        self.assertFalse(summarize_projects(rows).projects[0].all_unknown)


class EmptyProjectTests(unittest.TestCase):
    def test_a_remembered_group_with_nothing_left_is_listed_at_zero(self):
        """Deleting a project's last resource must not erase its name, and must
        not give it a bubble either."""
        overview = summarize_projects(
            [row(rid="a", project_id="p1", name="Live")],
            remembered=(("grp-0002", "Retired"),))
        by_name = {p.display_name: p for p in overview.projects}
        self.assertIn("Retired", by_name)
        self.assertEqual(by_name["Retired"].resource_count, 0)

    def test_an_empty_project_has_no_unused_percentage(self):
        """None, not 0%. The question does not apply, and 0 would rank it
        alongside the healthiest project in the account."""
        overview = summarize_projects([], remembered=(("grp-1", "Gone"),))
        self.assertIsNone(overview.projects[0].unused_pct)
        self.assertFalse(overview.projects[0].all_unknown)

    def test_a_remembered_group_that_still_has_a_row_is_not_duplicated(self):
        overview = summarize_projects(
            [row(rid="a", project_id="grp-1", name="Live")],
            remembered=(("grp-1", "Live"),))
        self.assertEqual(len(overview.projects), 1)
        self.assertEqual(overview.projects[0].resource_count, 1)


class NoCostTests(unittest.TestCase):
    def test_a_project_summary_carries_no_cost(self):
        """Removed with the project cost view: a figure that
        exists but is never drawn would still be read."""
        data = summarize_projects([row(rid="a", project_id="p1")]).projects[0].as_dict()
        self.assertFalse({"cost", "credits", "cost_completeness"} & set(data))


class OrderingAndNotesTests(unittest.TestCase):
    def test_buckets_sort_after_projects_however_big_they_are(self):
        """A large "Unassigned" heading the list would read as the account's
        biggest project."""
        rows = [row(rid=str(i), membership="unassigned") for i in range(20)]
        rows.append(row(rid="p", project_id="p1", name="Small"))
        overview = summarize_projects(rows)
        self.assertEqual(overview.projects[0].kind, KIND_PROJECT)
        self.assertEqual(overview.projects[-1].kind, KIND_UNASSIGNED)

    def test_an_all_unknown_project_is_called_out(self):
        rows = [row(rid="a", project_id="p1", name="p", usage=USAGE_UNKNOWN)]
        notes = summarize_projects(rows).notes
        self.assertTrue(any("no readable usage telemetry" in n for n in notes))

    def test_named_projects_are_told_apart_from_scan_inferred_ones(self):
        """What decides which view the overview opens on."""
        inferred = [row(rid="a", project_id="deployment:x")]
        inferred[0]["grouping_method"] = "deployment"
        self.assertFalse(summarize_projects(inferred).has_named_projects)
        inferred[0]["grouping_method"] = "named"
        self.assertTrue(summarize_projects(inferred).has_named_projects)


if __name__ == "__main__":
    unittest.main()
