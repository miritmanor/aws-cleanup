"""Console deep links: every scanned type has one, and the types whose URL is
assembled from more than resource_id get the right pieces."""

import unittest

from aws_resource_audit.present.html.console_links import CONSOLE_URLS, build_console_url

from .collectors_base import ACCOUNT, REGION
from .corpus import ALL_SERVICES
from .fakes import audit


def _row(service, resource_id, arn="", name=""):
    return audit.new_row(service, REGION, resource_id, name, "", "", None, True,
                         "ACTIVE", "", arn=arn, account=ACCOUNT)


class ConsoleLinkTests(unittest.TestCase):

    def test_every_resource_type_has_a_console_link(self):
        """A missing entry shows the ID as plain text in both front ends."""
        missing = [s for s in ALL_SERVICES if s not in CONSOLE_URLS]
        self.assertEqual(missing, [], f"add these to CONSOLE_URLS: {missing}")

    def test_every_type_builds_a_url_without_error(self):
        """A template naming a field nobody supplies raises KeyError at render."""
        for service in CONSOLE_URLS:
            with self.subTest(service=service):
                url = build_console_url(_row(service, "x/y", arn="arn:aws:svc:r:1:t/x"))
                self.assertTrue(url.startswith("https://"), url)

    def test_failed_lookup_row_has_no_link(self):
        row = audit.error_row("S3Bucket", REGION, "ERROR", "denied")
        self.assertEqual(build_console_url(row), "")

    def test_unknown_type_has_no_link(self):
        self.assertEqual(build_console_url(_row("NotAType", "x")), "")

    def test_route53_zone_links_to_its_records(self):
        url = build_console_url(_row("Route53HostedZone", "Z123"))
        self.assertTrue(url.endswith("#ListRecordSets/Z123"), url)

    def test_ecs_service_splits_cluster_and_service(self):
        url = build_console_url(_row("ECSService", "web/api"))
        self.assertIn("/clusters/web/services/api/", url)

    def test_eventbridge_rule_on_a_custom_bus(self):
        arn = f"arn:aws:events:{REGION}:{ACCOUNT}:rule/orders/nightly"
        url = build_console_url(_row("EventBridgeRule", "nightly", arn=arn))
        self.assertIn("#/eventbus/orders/rules/nightly", url)

    def test_eventbridge_rule_on_the_default_bus(self):
        arn = f"arn:aws:events:{REGION}:{ACCOUNT}:rule/nightly"
        url = build_console_url(_row("EventBridgeRule", "nightly", arn=arn))
        self.assertIn("#/eventbus/default/rules/nightly", url)

    def test_eventbridge_schedule_names_its_group(self):
        arn = f"arn:aws:scheduler:{REGION}:{ACCOUNT}:schedule/jobs/hourly"
        url = build_console_url(_row("EventBridgeSchedule", "hourly", arn=arn))
        self.assertIn("#schedules/jobs/hourly", url)

    def test_waf_web_acl_uses_the_id_from_its_arn(self):
        arn = f"arn:aws:wafv2:{REGION}:{ACCOUNT}:regional/webacl/main/abc-123"
        url = build_console_url(_row("WAFWebACL", "main", arn=arn))
        self.assertIn("/web-acl/main/abc-123/overview", url)

    def test_rds_snapshot_kind_follows_the_arn(self):
        cluster = f"arn:aws:rds:{REGION}:{ACCOUNT}:cluster-snapshot:s1"
        instance = f"arn:aws:rds:{REGION}:{ACCOUNT}:snapshot:s1"
        self.assertIn("#db-cluster-snapshot:id=s1",
                      build_console_url(_row("RDSSnapshot", "s1", arn=cluster)))
        self.assertIn("#db-snapshot:id=s1",
                      build_console_url(_row("RDSSnapshot", "s1", arn=instance)))

    def test_sqs_queue_links_by_its_encoded_url(self):
        url = build_console_url(_row("SQSQueue", "jobs"))
        self.assertIn(f"#/queues/https%3A%2F%2Fsqs.{REGION}.amazonaws.com%2F{ACCOUNT}%2Fjobs", url)

    def test_ecr_repository_keeps_slashes_in_its_name(self):
        url = build_console_url(_row("ECRRepository", "team/app"))
        self.assertIn(f"/private/{ACCOUNT}/team/app?", url)

    def test_studio_app_links_to_its_domain(self):
        url = build_console_url(_row("SageMakerStudioApp", "d-abc/alice/JupyterServer/default"))
        self.assertTrue(url.endswith("#/studio/d-abc"), url)
