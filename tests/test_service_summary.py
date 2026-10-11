"""The overview's By service numbers, the bill lines behind its cost circles,
and the payload both front ends read."""

import unittest
from datetime import date
from decimal import Decimal

from . import FROZEN_ACCOUNT  # noqa: F401  (import for the clock/identity pin)
from .fakes import make_row

from aws_resource_audit.analyze.project_summary import summarize_projects
from aws_resource_audit.analyze.service_summary import KIND_BILL, summarize_services
from aws_resource_audit.billing import BillingAmount, BillingResult, not_queried
from aws_resource_audit.present.bubbles import (
    PROJECT_METRICS,
    VIEW_PROJECT,
    VIEW_SERVICE,
    overview_payload,
)


def billing(*amounts):
    return BillingResult(
        queried=True, currency="USD", complete=True,
        period_start=date(2026, 5, 2), period_end=date(2026, 6, 1),
        amounts=tuple(BillingAmount(s, r, Decimal(a)) for s, r, a in amounts))


def rows():
    return [make_row("EC2Instance", "i-1"), make_row("SecurityGroup", "sg-1"),
            make_row("SecurityGroup", "sg-2"), make_row("EBSVolume", "vol-1"),
            make_row("S3Bucket", "b1", region="eu-west-1")]


class ServiceCountTests(unittest.TestCase):
    def test_resource_types_fold_into_their_aws_service(self):
        by_name = {s.display_name: s for s in summarize_services(rows()).services}
        self.assertEqual(by_name["EC2"].resource_count, 3)
        self.assertEqual(by_name["EC2"].type_counts,
                         (("SecurityGroup", 2), ("EC2Instance", 1)))
        # EBS is its own AWS product, not part of EC2 - see aws_services.py.
        self.assertEqual(by_name["EBS"].resource_count, 1)

    def test_every_row_is_counted_once(self):
        services = summarize_services(rows()).services
        self.assertEqual(sum(s.resource_count for s in services), len(rows()))

    def test_largest_service_first(self):
        self.assertEqual(summarize_services(rows()).services[0].display_name, "EC2")


class BillLineTests(unittest.TestCase):
    def test_a_bill_line_is_summed_over_regions_and_named_as_aws_names_it(self):
        overview = summarize_services(rows(), billing(
            ("EC2 - Other", "us-east-1", "10"), ("EC2 - Other", "eu-west-1", "5"),
            ("Tax", "", "2")))
        lines = {line.display_name: line for line in overview.bill_lines}
        self.assertEqual(lines["EC2 - Other"].cost, Decimal("15"))
        self.assertEqual(lines["EC2 - Other"].kind, KIND_BILL)
        self.assertIn("Tax", lines)

    def test_credits_are_not_drawn_but_are_reported(self):
        overview = summarize_services(rows(), billing(
            ("AWS Lambda", "us-east-1", "4"), ("Credits", "", "-3")))
        self.assertEqual([line.display_name for line in overview.bill_lines], ["AWS Lambda"])
        self.assertTrue(any("credits" in n for n in overview.notes))

    def test_not_queried_has_no_bill_lines_and_says_why(self):
        overview = summarize_services(rows(), not_queried())
        self.assertEqual(overview.bill_lines, ())
        self.assertTrue(any("not queried" in n for n in overview.notes))


class PayloadTests(unittest.TestCase):
    def payload(self, project_rows=None, bill=None):
        project_rows = project_rows or rows()
        return overview_payload(
            summarize_projects(project_rows),
            summarize_services(project_rows, bill or billing(("AWS Lambda", "us-east-1", "4"))),
            default_metric="resource_count")

    def views(self, payload):
        return {v["id"]: v for v in payload["views"]}

    def test_opens_on_services_until_someone_names_a_project(self):
        self.assertEqual(self.payload()["default_view"], VIEW_SERVICE)
        named = rows()
        named[0].update(project_id="group:g1", project_group="web",
                        grouping_method="named")
        self.assertEqual(self.payload(named)["default_view"], VIEW_PROJECT)

    def test_the_project_view_has_no_cost_metric(self):
        project = self.views(self.payload())[VIEW_PROJECT]
        self.assertEqual([m[0] for m in project["metrics"]], list(PROJECT_METRICS))
        self.assertNotIn("cost", project["packs"])

    def test_service_cost_circles_and_list_are_the_bill_lines(self):
        service = self.views(self.payload())[VIEW_SERVICE]
        self.assertEqual([b["id"] for b in service["packs"]["cost"]], ["bill:AWS Lambda"])
        self.assertEqual(service["lists"]["cost"], ["bill:AWS Lambda"])
        self.assertTrue(all(i.startswith("svc:") for i in service["lists"]["resource_count"]))

    def test_every_circle_names_an_item_of_its_view(self):
        for view in self.payload()["views"]:
            ids = {item["id"] for item in view["items"]}
            for circles in view["packs"].values():
                self.assertTrue({b["id"] for b in circles} <= ids)


if __name__ == "__main__":
    unittest.main()
