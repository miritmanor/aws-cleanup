"""audit_groups' oversized-cluster finding, independent of the golden corpus's size."""

import unittest

from . import FROZEN_ACCOUNT  # noqa: F401  (import for the clock/identity pin)
from .fakes import audit, make_row


def rows_with_project(in_project, total):
    rows = [make_row("LambdaFunction", f"fn{i}") for i in range(total)]
    for row in rows[:in_project]:
        row["project_group"], row["project_id"] = "big", "tag:big"
    return rows


class OversizedClusterTests(unittest.TestCase):
    def test_a_cluster_over_the_threshold_is_flagged(self):
        findings = audit.audit_groups(rows_with_project(5, 10))
        self.assertEqual([name for name, _m, _f in findings.large_clusters], ["big"])

    def test_a_small_share_is_not_flagged(self):
        self.assertEqual(audit.audit_groups(rows_with_project(5, 100)).large_clusters, [])


class ProjectIdentityTests(unittest.TestCase):
    """Two projects can share a label; the audit counts and judges them apart."""

    def test_two_projects_with_one_label_are_two_clusters(self):
        rows = [make_row("LambdaFunction", f"fn{i}") for i in range(20)]
        for row in rows[:3]:
            row["project_group"], row["project_id"] = "shop", "tag:shop"
        for row in rows[3:6]:
            row["project_group"], row["project_id"] = "shop", "group:7"
        findings = audit.audit_groups(rows)
        self.assertEqual(findings.cluster_count, 2)
        # Three of twenty each is under the threshold; pooled as six it was not.
        self.assertEqual(findings.large_clusters, [])

    def test_a_shared_label_is_told_apart_by_its_id_in_the_report(self):
        rows = [make_row("LambdaFunction", f"fn{i}") for i in range(8)]
        for row in rows[:4]:
            row["project_group"], row["project_id"] = "shop", "tag:shop"
        for row in rows[4:]:
            row["project_group"], row["project_id"] = "shop", "group:7"
        names = sorted(name for name, _m, _f in audit.audit_groups(rows).large_clusters)
        self.assertEqual(names, ["shop (group:7)", "shop (tag:shop)"])


if __name__ == "__main__":
    unittest.main()
