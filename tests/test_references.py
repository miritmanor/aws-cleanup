"""How each reference kind is worded in the connections column: links explain themselves
from both ends, and report-only links say so inline."""

import unittest

from .fakes import make_row

import aws_resource_audit as audit
from aws_resource_audit.present.references import render_reference


def ref(kind, **fields):
    base = {"kind": kind, "rel": "reads", "evidence": "env var TABLE",
            "confidence": "authoritative", "conn_type": "lambda.any.env-var-arn-value",
            "grouping": True}
    base.update(fields)
    return base


class WordingTests(unittest.TestCase):
    def test_a_resolved_link_names_the_target_and_the_evidence(self):
        text = render_reference(ref("resolved", target_service="DynamoDBTable",
                                    target_id="orders", target_idx=1))
        self.assertEqual(text, "reads DynamoDBTable:orders [via env var TABLE]")

    def test_the_same_link_is_worded_from_the_target_end_too(self):
        text = render_reference(ref("incoming", source_service="LambdaFunction",
                                    source_id="worker", source_idx=0))
        self.assertEqual(
            text, "used by LambdaFunction:worker (reads) [via env var TABLE]")

    def test_an_authoritative_link_says_nothing_about_confidence(self):
        """It is the default and the common case; annotating it would make the
        column unreadable and devalue the annotation where it matters."""
        text = render_reference(ref("resolved", target_service="S3Bucket",
                                    target_id="assets", target_idx=1))
        self.assertNotIn("authoritative", text)

    def test_a_heuristic_link_is_labelled(self):
        text = render_reference(ref("resolved", confidence="heuristic",
                                    target_service="S3Bucket", target_id="assets",
                                    target_idx=1))
        self.assertIn(", heuristic", text)

    def test_a_report_only_link_explains_why_it_did_not_group(self):
        text = render_reference(ref("resolved", grouping=False,
                                    target_service="S3Bucket", target_id="assets",
                                    target_idx=1))
        self.assertIn("report-only: shown but not used for project grouping", text)

    def test_a_dangling_reference_says_where_it_pointed(self):
        text = render_reference(ref("dangling", grouping=False, target_id="gone",
                                    target_service="DynamoDBTable", target_idx=None))
        self.assertIn("DANGLING -> gone", text)
        self.assertIn("NOT FOUND in this scan", text)

    def test_a_dangling_reference_is_not_labelled_report_only(self):
        """A dangling record's grouping=False is not a "report-only" claim."""
        text = render_reference(ref("dangling", grouping=False, target_id="gone",
                                    target_service="DynamoDBTable", target_idx=None))
        self.assertNotIn("report-only", text)

    def test_a_collision_names_what_it_actually_matched(self):
        text = render_reference(ref("collision", target_id="assets",
                                    expected_service="AMI", matched=["S3Bucket"],
                                    grouping=False))
        self.assertIn("matched S3Bucket:assets but expected a AMI", text)
        self.assertIn("coincidental name collision", text)

    def test_a_refused_prefix_grant_says_it_read_the_pattern(self):
        """A silent refusal reads as never having looked at the policy."""
        text = render_reference(ref("prefix-refused", target_id="orders-",
                                    target_service="DynamoDBTable",
                                    match_count=40, limit=12, grouping=False))
        self.assertIn("'orders-*' matches 40 scanned", text)
        self.assertIn("over the 12 limit", text)

    def test_an_unknown_kind_raises_rather_than_rendering_nothing(self):
        with self.assertRaises(ValueError):
            render_reference(ref("invented"))


class ColumnTests(unittest.TestCase):
    def test_the_collectors_own_prose_comes_first_and_survives(self):
        """VPC/subnet/security-group text has no structured form yet, so it is
        carried through rather than re-worded."""
        row = make_row("EC2Instance", "i-0abc", "web",
                       connections="VPC=vpc-1, SGs=[sg-1]")
        row["_references"] = [ref("resolved", target_service="AMI",
                                  target_id="ami-1", target_idx=1)]
        text = audit.render_connections(row)
        self.assertTrue(text.startswith("VPC=vpc-1, SGs=[sg-1] | "))
        self.assertIn("AMI:ami-1", text)

    def test_a_row_with_nothing_to_say_renders_empty(self):
        self.assertEqual(audit.render_connections(make_row("S3Bucket", "b")), "")


if __name__ == "__main__":
    unittest.main()
