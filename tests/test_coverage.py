"""A failed lookup stays a failure all the way to the verdict: "found nothing" and
"not allowed to look" never merge, and the second never lowers removal risk."""

import unittest

from . import FROZEN_ACCOUNT
from .fakes import FakeClient, FakeSession, audit, client_error, make_row

from aws_resource_audit import coverage as cov

EAST = "us-east-1"
WEST = "us-west-2"


def ledger_with(*entries):
    """A ledger holding exactly the coverage the test cares about."""
    ledger = cov.Ledger()
    for service, scope, status in entries:
        ledger.record(cov.INVENTORY, status, service=service, scope=scope,
                      collector=service, operation="List")
    return ledger


def dangling(row):
    return [r for r in row["_references"] if r["kind"] == "dangling"]


class ReferenceStateTests(unittest.TestCase):
    """Why a reference resolved to nothing, not merely that it did."""

    def _api_referencing_a_missing_lambda(self):
        api = make_row("APIGatewayRestApi", "rest1", region=EAST)
        audit.add_edge(api, "orders-fn", "integrates", "REST API integration URI",
                       conn_type="apigateway.lambda.integration",
                       target_service="LambdaFunction")
        return api

    def test_denied_lambda_collection_leaves_the_reference_unknown(self):
        """The headline case. A denied collector must not make the API that
        points into it look like a cleanup candidate."""
        api = self._api_referencing_a_missing_lambda()
        audit.resolve_edges([api], coverage=ledger_with(
            ("LambdaFunction", EAST, cov.DENIED)))

        self.assertEqual(dangling(api)[0]["state"], "lookup_failed")
        self.assertTrue(api["risk_if_removed"].startswith("UNKNOWN"),
                        api["risk_if_removed"])
        self.assertNotIn("deleted", api["risk_if_removed"])

    def test_an_unrequested_region_is_out_of_scope(self):
        api = self._api_referencing_a_missing_lambda()
        audit.resolve_edges([api], coverage=ledger_with(
            ("LambdaFunction", EAST, cov.NOT_REQUESTED)))

        self.assertEqual(dangling(api)[0]["state"], "out_of_scope")
        self.assertTrue(api["risk_if_removed"].startswith("UNKNOWN"))

    def test_a_complete_listing_confirms_the_target_is_gone(self):
        """A complete enumeration of the only possible scope proves absence."""
        api = self._api_referencing_a_missing_lambda()
        audit.resolve_edges([api], coverage=ledger_with(
            ("LambdaFunction", EAST, cov.COMPLETE)))

        self.assertEqual(dangling(api)[0]["state"], "confirmed_missing")
        self.assertTrue(api["risk_if_removed"].startswith("LOW"),
                        api["risk_if_removed"])

    def test_a_filtered_listing_cannot_confirm_absence(self):
        """describe_images(Owners=["self"]) omits every public AMI, so a
        missing one is routine rather than deleted."""
        inst = make_row("EC2Instance", "i-1", region=EAST)
        audit.add_edge(inst, "ami-public", "launched from AMI", "instance ImageId",
                       conn_type="ec2.ami.image-id", target_service="AMI",
                       assert_exists=True)
        audit.resolve_edges([inst], coverage=ledger_with(("AMI", EAST, cov.COMPLETE)))

        self.assertEqual(dangling(inst)[0]["state"], "not_found_in_inventory")
        self.assertFalse(inst["risk_if_removed"].startswith("LOW"))

    def test_one_scanned_region_cannot_confirm_an_unscoped_reference(self):
        """A bare name could name a table in any region. Sweeping one of
        seventeen proves nothing about the other sixteen."""
        fn = make_row("LambdaFunction", "sync", region=EAST)
        audit.add_edge(fn, "orders", "may use", "env var TABLE_NAME",
                       conn_type="lambda.any.env-var-bare-name",
                       target_service="DynamoDBTable", assert_exists=True)
        audit.resolve_edges([fn], coverage=ledger_with(
            ("DynamoDBTable", EAST, cov.COMPLETE)))

        self.assertEqual(dangling(fn)[0]["state"], "not_found_in_inventory")

    def test_a_reference_to_another_account_is_out_of_scope(self):
        api = make_row("APIGatewayRestApi", "rest1", region=EAST)
        audit.add_edge(api, "orders-fn", "integrates", "REST API integration URI",
                       conn_type="apigateway.lambda.integration",
                       target_service="LambdaFunction",
                       target_account="999988887777")
        audit.resolve_edges([api], coverage=ledger_with(
            ("LambdaFunction", EAST, cov.COMPLETE)))

        self.assertEqual(dangling(api)[0]["state"], "out_of_scope")

    def test_the_states_survive_serialization(self):
        """A rendered report is built from the snapshot, not from memory."""
        api = self._api_referencing_a_missing_lambda()
        audit.resolve_edges([api], coverage=ledger_with(
            ("LambdaFunction", EAST, cov.DENIED)))
        restored = audit.restore_row(audit.snapshot_row(api))

        self.assertEqual(dangling(restored)[0]["state"], "lookup_failed")


class ErrorClassificationTests(unittest.TestCase):
    """A refusal and a breakage are different coverage, and read differently."""

    def test_access_denied_is_denied(self):
        self.assertEqual(
            cov.classify_error("An error occurred (AccessDenied) when calling ..."),
            cov.DENIED)

    def test_unauthorized_operation_is_denied(self):
        self.assertEqual(
            cov.classify_error("UnauthorizedOperation: not authorized to perform"),
            cov.DENIED)

    def test_an_operation_aws_disabled_is_unsupported_not_denied(self):
        self.assertEqual(
            cov.classify_error("GetWidgets operation is currently disabled.",
                               "AccessDeniedException"),
            cov.UNSUPPORTED)

    def test_anything_else_is_a_failure(self):
        self.assertEqual(cov.classify_error("Connection reset by peer"), cov.FAILED)

    def test_not_found_is_not_a_denial(self):
        """A targeted not-found stays separate from a refusal."""
        self.assertEqual(
            cov.classify_error("An error occurred (ResourceNotFoundException) ..."),
            cov.FAILED)
        self.assertNotEqual(
            cov.classify_error("ResourceNotFoundException"),
            cov.classify_error("AccessDeniedException"))


class LedgerTests(unittest.TestCase):

    def test_the_worst_status_wins(self):
        """Nine good regions and one denial is not complete coverage."""
        ledger = ledger_with(("SQSQueue", EAST, cov.COMPLETE),
                             ("SQSQueue", WEST, cov.DENIED))
        self.assertEqual(ledger.status("SQSQueue"), cov.DENIED)
        self.assertEqual(ledger.status("SQSQueue", EAST), cov.COMPLETE)

    def test_silence_is_not_a_status(self):
        """A snapshot older than coverage must not read as 'all fine'."""
        self.assertIsNone(cov.Ledger().status("SQSQueue"))

    def test_a_failed_tag_lookup_does_not_condemn_the_enumeration(self):
        ledger = cov.Ledger()
        ledger.record(cov.INVENTORY, cov.COMPLETE, service="S3Bucket", scope="global")
        ledger.record(cov.TAGS, cov.DENIED, service="S3Bucket", scope="global")

        self.assertEqual(ledger.status("S3Bucket", capability=cov.INVENTORY),
                         cov.COMPLETE)
        self.assertEqual(ledger.status("S3Bucket", capability=cov.TAGS), cov.DENIED)


class PaginationTests(unittest.TestCase):
    """Half an inventory plus "this is half" beats no inventory at all."""

    def test_a_paginator_failure_keeps_what_it_read(self):
        client = FakeSession({"sqs": {"list_queues": client_error()}}).client("sqs")
        with cov.ledger_of() as ledger:
            items, error = audit.paged(client, "list_queues", "QueueUrls")
        self.assertEqual(items, [])
        self.assertTrue(error)
        self.assertEqual(ledger.status("", None), cov.DENIED)

    def test_a_collector_keeps_the_resources_it_read_before_the_failure(self):
        """A real collector whose paginator fails after one good page keeps that page
        and reports PARTIAL."""
        pages = [{"Volumes": [{"VolumeId": "vol-1", "Size": 8, "State": "available",
                               "CreateTime": audit.now(), "Attachments": []}]},
                 client_error(operation="DescribeVolumes")]

        class FailingPaginator:
            def paginate(self, **kwargs):
                for page in pages:
                    if isinstance(page, BaseException):
                        raise page
                    yield page

        class FailingEc2:
            def can_paginate(self, _operation):
                return True

            def get_paginator(self, _operation):
                return FailingPaginator()

        class HalfBrokenSession:
            """EC2 pages then fails; CloudWatch behaves normally."""

            region_name = EAST

            def __init__(self):
                self._cw = FakeSession({"cloudwatch": {
                    "get_metric_statistics": {"Datapoints": []}}}).client("cloudwatch")

            def client(self, service_name, **kwargs):
                return FailingEc2() if service_name == "ec2" else self._cw

        with cov.ledger_of() as ledger:
            rows = audit.collect_ebs_volumes(HalfBrokenSession(), EAST)

        self.assertEqual([r["resource_id"] for r in rows if r["flag"] != "ERROR"],
                         ["vol-1"], "the volume read before the failure is kept")
        # No scope: the collector is called directly, outside collect_all's context.
        self.assertEqual(ledger.status("EBSVolume"), cov.PARTIAL)

    def test_an_operation_with_no_paginator_still_pages(self):
        """Some list operations (events:ListEventBuses) have no paginator; paged() pages
        them by hand instead of reporting a failure."""
        def respond(**kwargs):
            if kwargs.get("NextToken") == "t1":
                return {"EventBuses": [{"Name": "orders-bus"}]}
            return {"EventBuses": [{"Name": "default"}], "NextToken": "t1"}

        # not_pageable makes the fake refuse get_paginator exactly as the real
        # client does, so a regression cannot quietly pass here.
        client = FakeClient("events", {"list_event_buses": respond},
                            not_pageable=["list_event_buses"])

        with cov.ledger_of() as ledger:
            items, error = audit.paged(client, "list_event_buses", "EventBuses")

        self.assertIsNone(error)
        self.assertEqual([b["Name"] for b in items], ["default", "orders-bus"])
        self.assertEqual([kwargs.get("NextToken") for _op, kwargs in client.calls],
                         [None, "t1"], "the second call carries the first's token")
        self.assertEqual(ledger.entries[0].status, cov.COMPLETE)

    def test_a_clean_pagination_reports_complete_and_a_count(self):
        client = FakeSession({"sqs": {"list_queues": [
            {"QueueUrls": ["a", "b"]}, {"QueueUrls": ["c"]}]}}).client("sqs")
        with cov.ledger_of() as ledger:
            items, error = audit.paged(client, "list_queues", "QueueUrls")
        self.assertEqual(items, ["a", "b", "c"])
        self.assertIsNone(error)
        self.assertEqual(ledger.entries[0].status, cov.COMPLETE)
        self.assertEqual(ledger.entries[0].count, 3)


class Ec2UsageCompletenessTests(unittest.TestCase):
    """An empty reference set only means "unused" if the producers ran."""

    def test_a_failed_producer_blocks_the_unreferenced_conclusion(self):
        usage = audit.new_ec2_usage_registry()
        usage["complete"] = False

        session = FakeSession({"ec2": {"describe_key_pairs": {"KeyPairs": [
            {"KeyName": "deploy", "CreateTime": audit.now()}]}}})
        rows = audit.collect_key_pairs(session, EAST, usage=usage)

        self.assertIn("UNKNOWN", rows[0]["flag"])
        self.assertNotIn("STALE", rows[0]["flag"])

    def test_a_complete_producer_still_reports_unused(self):
        usage = audit.new_ec2_usage_registry()   # complete, and empty

        session = FakeSession({"ec2": {"describe_key_pairs": {"KeyPairs": [
            {"KeyName": "deploy", "CreateTime": audit.now()}]}}})
        rows = audit.collect_key_pairs(session, EAST, usage=usage)

        self.assertIn("STALE", rows[0]["flag"])

    def test_a_denied_instance_sweep_marks_the_registry_incomplete(self):
        usage = audit.new_ec2_usage_registry()
        session = FakeSession({
            "ec2": {"describe_instances": client_error()},
            "cloudwatch": {"get_metric_statistics": {"Datapoints": []}},
        })
        audit.collect_ec2_instances(session, EAST, usage=usage)

        self.assertFalse(usage["complete"])


class DefaultVpcTests(unittest.TestCase):
    """An unknown default VPC must not become "every VPC is deliberate"."""

    def test_a_denied_lookup_reports_unknown(self):
        session = FakeSession({"ec2": {"describe_vpcs": client_error()}})
        ids, known = audit.collect_default_vpc_ids(session, EAST)
        self.assertEqual(ids, set())
        self.assertFalse(known)

    def test_a_successful_lookup_reports_known(self):
        session = FakeSession({"ec2": {"describe_vpcs": {
            "Vpcs": [{"VpcId": "vpc-default"}]}}})
        ids, known = audit.collect_default_vpc_ids(session, EAST)
        self.assertEqual(ids, {"vpc-default"})
        self.assertTrue(known)

    def test_unknown_defaults_suppress_vpc_grouping(self):
        import contextlib
        import io

        rows = [make_row("EC2Instance", "i-1", vpc_id="vpc-shared"),
                make_row("RDSInstance", "db-1", vpc_id="vpc-shared")]
        with contextlib.redirect_stdout(io.StringIO()):
            audit.apply_project_grouping(rows, default_vpcs_known=False)

        self.assertEqual([r["project_group"] for r in rows], ["", ""],
                         "an unknown default VPC must not merge two resources")


class ReportTests(unittest.TestCase):
    """Coverage has to reach the outputs, or it changes nobody's mind."""

    def test_the_html_banner_names_the_gaps(self):
        html = audit.render_html_report(
            [make_row("S3Bucket", "assets", region="global")],
            coverage=ledger_with(("LambdaFunction", EAST, cov.DENIED)).as_dicts())
        self.assertIn("did not complete", html)
        self.assertIn("LambdaFunction", html)

    def test_complete_coverage_adds_no_banner(self):
        html = audit.render_html_report(
            [make_row("S3Bucket", "assets", region="global")],
            coverage=ledger_with(("LambdaFunction", EAST, cov.COMPLETE)).as_dicts())
        self.assertNotIn("did not complete", html)

    def test_the_audit_markdown_lists_the_gaps(self):
        text = audit.render_coverage(
            ledger_with(("SQSQueue", WEST, cov.DENIED)).as_dicts())
        self.assertIn("SQSQueue", text)
        self.assertIn(WEST, text)
        self.assertIn("denied", text)


class SnapshotCoverageTests(unittest.TestCase):

    def test_coverage_round_trips_through_the_snapshot(self):
        ledger = ledger_with(("SQSQueue", EAST, cov.DENIED))
        payload = audit.build_snapshot([], {}, {}, FROZEN_ACCOUNT, [EAST],
                                       audit.now(), coverage=ledger.as_dicts())
        self.assertEqual(payload["coverage"][0]["status"], cov.DENIED)
        self.assertEqual(payload["coverage"][0]["service"], "SQSQueue")


if __name__ == "__main__":
    unittest.main()
