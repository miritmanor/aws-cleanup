"""Connection types for integration: SQS, SNS, EventBridge rules, buses and
schedules, CloudTrail and CloudWatch Logs. API Gateway: test_connections_apigateway."""

import json
import unittest

from .fakes import FakeSession, make_row, audit, recent
from .connections_base import (
    ACCOUNT,
    ConnectionTypeTestCase,
    REGION,
    epoch_ms,
    iam_session,
    sns_session,
    sqs_session,
)


class MessagingConnectionTests(ConnectionTypeTestCase):

    def test_sqs_redrive_policy(self):
        def build():
            session = sqs_session("orders-queue", attributes={
                "RedrivePolicy": json.dumps({
                    "deadLetterTargetArn": f"arn:aws:sqs:{REGION}:{ACCOUNT}:orders-dlq",
                    "maxReceiveCount": 5,
                }),
            })
            rows = audit.collect_sqs_queues(session, REGION)
            return rows + [make_row("SQSQueue", "orders-dlq")]
        self.assert_three_states(
            "sqs.sqs.redrive-policy", build, "orders-queue", "orders-dlq")

    def test_sns_subscription_to_lambda(self):
        def build():
            session = sns_session("orders-events", subscriptions=[{
                "Protocol": "lambda",
                "Endpoint": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:orders-fn",
            }])
            rows = audit.collect_sns_topics(session, REGION)
            return rows + [make_row("LambdaFunction", "orders-fn")]
        self.assert_three_states(
            "sns.any.subscription", build, "orders-events", "orders-fn")

    def test_sns_subscription_to_sqs(self):
        def build():
            session = sns_session("orders-events", subscriptions=[{
                "Protocol": "sqs",
                "Endpoint": f"arn:aws:sqs:{REGION}:{ACCOUNT}:orders-queue",
            }])
            rows = audit.collect_sns_topics(session, REGION)
            return rows + [make_row("SQSQueue", "orders-queue")]
        self.assert_three_states(
            "sns.any.subscription", build, "orders-events", "orders-queue")

    def test_non_resource_subscribers_are_reported_but_never_linked(self):
        """An email or HTTPS subscriber explains what a topic is for, but there
        is no AWS resource on the other end to match a row against."""
        session = sns_session("orders-events", subscriptions=[
            {"Protocol": "email", "Endpoint": "ops@example.com"},
            {"Protocol": "https", "Endpoint": "https://hooks.example.com/sns"},
        ])
        row = audit.collect_sns_topics(session, REGION)[0]
        self.assertIn("ops@example.com", audit.render_connections(row))
        self.assertEqual(row.get("_edges", []), [])

    def test_a_dead_letter_queue_is_not_reported_as_an_orphan(self):
        """A DLQ sits idle by design; the redrive link keeps it from looking abandoned."""
        session = sqs_session("orders-queue", attributes={
            "RedrivePolicy": json.dumps({
                "deadLetterTargetArn": f"arn:aws:sqs:{REGION}:{ACCOUNT}:orders-dlq",
                "maxReceiveCount": 5,
            }),
        })
        rows = audit.collect_sqs_queues(session, REGION)
        rows.append(make_row("SQSQueue", "orders-dlq", flag="ACTIVE"))
        audit.resolve_edges(rows)
        dlq = self._row(rows, "orders-dlq")
        self.assertIn("used by", audit.render_connections(dlq))
        self.assertIn("orders-queue", audit.render_connections(dlq))

    def test_iam_role_granting_access_to_a_queue_now_resolves(self):
        """Adding SQS/SNS to the ARN mapping also makes every ARN-driven
        mechanism able to reach them - this is the IAM one, for free."""
        with self._isolated("iamrole.any.resource-grant", audit.CONN_ON):
            session = iam_session(
                roles=[{"RoleName": "orders-role", "Path": "/", "CreateDate": recent(),
                        "RoleLastUsed": {"LastUsedDate": recent()},
                        "AssumeRolePolicyDocument": {"Statement": []}}],
                extra={
                    "list_role_policies": {"PolicyNames": ["inline"]},
                    "get_role_policy": {"PolicyDocument": {"Statement": [{
                        "Effect": "Allow", "Action": "sqs:SendMessage",
                        "Resource": f"arn:aws:sqs:{REGION}:{ACCOUNT}:orders-queue",
                    }]}},
                },
            )
            rows = audit.collect_iam(session)
            rows.append(make_row("SQSQueue", "orders-queue"))
            links = audit.resolve_edges(rows)
            self.assertTrue(links)
            self.assertIn("SQSQueue:orders-queue", audit.render_connections(self._row(rows, "orders-role")))


class EventBridgeTests(ConnectionTypeTestCase):
    """What makes something run when nothing calls it: a Lambda invoked only by a rule
    must not look unreferenced."""

    def _events_session(self, schedule="rate(1 day)", state="ENABLED",
                        bus="default", target_arn=None):
        target_arn = target_arn or f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:nightly-fn"
        return FakeSession({"events": {
            "list_event_buses": {"EventBuses": [{"Name": bus}]},
            "list_rules": {"Rules": [{
                "Name": "nightly-report", "State": state,
                "ScheduleExpression": schedule,
                "Arn": f"arn:aws:events:{REGION}:{ACCOUNT}:rule/nightly-report",
            }]},
            "list_targets_by_rule": {"Targets": [{"Id": "1", "Arn": target_arn}]},
        }})

    def _rule_rows(self, **kwargs):
        rows = audit.collect_eventbridge_rules(self._events_session(**kwargs), REGION)
        return rows + [make_row("LambdaFunction", "nightly-fn")]

    def test_eventbridgerule_any_target(self):
        self.assert_three_states("eventbridgerule.any.target",
                                 lambda: self._rule_rows(),
                                 "nightly-report", "nightly-fn")

    def test_eventbridgerule_iamrole_target_role(self):
        def build():
            session = self._events_session()
            session.get("events").responses["list_targets_by_rule"] = {
                "Targets": [{
                    "Id": "1",
                    "Arn": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:nightly-fn",
                    "RoleArn": f"arn:aws:iam::{ACCOUNT}:role/events-role",
                }]}
            rows = audit.collect_eventbridge_rules(session, REGION)
            return rows + [make_row("LambdaFunction", "nightly-fn"),
                           make_row("IAMRole", "events-role", region="global")]
        self.assert_three_states("eventbridgerule.iamrole.target-role", build,
                                 "nightly-report", "events-role")

    def test_a_scheduled_lambda_is_no_longer_unreferenced(self):
        rows = self._rule_rows()
        audit.resolve_edges(rows)
        fn = self._row(rows, "nightly-fn")

        self.assertIn("used by", audit.render_connections(fn))
        self.assertTrue(fn["risk_if_removed"].startswith("HIGH"),
                        fn["risk_if_removed"])

    def test_rules_on_a_custom_bus_are_covered(self):
        """Rules on a custom bus are the application-specific ones, so
        covering only the default bus would miss exactly those."""
        rows = self._rule_rows(bus="orders-bus")
        rule = next(r for r in rows if r["service"] == "EventBridgeRule")
        self.assertIn("orders-bus", rule["description"])
        self.assertEqual([r["resource_id"] for r in rows if r["service"] == "EventBridgeBus"],
                         ["orders-bus"])

    def test_eventbridgerule_eventbridgebus_membership(self):
        self.assert_three_states("eventbridgerule.eventbridgebus.membership",
                                 lambda: self._rule_rows(bus="orders-bus"),
                                 "nightly-report", "orders-bus")

    def test_the_default_bus_is_not_a_row(self):
        self.assertNotIn("EventBridgeBus", {r["service"] for r in self._rule_rows()})

    def test_a_schedule_is_not_evidence_that_anything_ran(self):
        rows = self._rule_rows()
        rule = rows[0]
        self.assertNotIn("ACTIVE", rule["flag"])
        self.assertIn("not evidence", rule["notes"])

    def test_a_disabled_rule_says_disabled_not_unused(self):
        rows = self._rule_rows(state="DISABLED")
        self.assertIn("DISABLED", rows[0]["flag"])

    def test_an_uninventoried_target_is_not_invented(self):
        state_machine = f"arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:orders"
        rows = self._rule_rows(target_arn=state_machine)
        before = len(rows)
        audit.resolve_edges(rows)
        self.assertEqual(len(rows), before)


class SchedulerTests(ConnectionTypeTestCase):
    """EventBridge Scheduler is a separate API from rules."""

    def _rows(self, state="ENABLED", dead_letter=None):
        target = {"Arn": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:cleanup-fn",
                  "RoleArn": f"arn:aws:iam::{ACCOUNT}:role/scheduler-role",
                  "RetryPolicy": {"MaximumRetryAttempts": 3}}
        if dead_letter:
            target["DeadLetterConfig"] = {"Arn": dead_letter}
        session = FakeSession({"scheduler": {
            "list_schedules": {"Schedules": [{"Name": "weekly-cleanup",
                                              "GroupName": "batch"}]},
            "get_schedule": {
                "Name": "weekly-cleanup", "GroupName": "batch", "State": state,
                "ScheduleExpression": "cron(0 3 ? * MON *)",
                "ScheduleExpressionTimezone": "UTC",
                "CreationDate": recent(),
                "Arn": f"arn:aws:scheduler:{REGION}:{ACCOUNT}:schedule/batch/weekly-cleanup",
                "Target": target,
            },
        }})
        rows = audit.collect_scheduler_schedules(session, REGION)
        return rows + [make_row("LambdaFunction", "cleanup-fn"),
                       make_row("IAMRole", "scheduler-role", region="global")]

    def test_eventbridgeschedule_any_target(self):
        self.assert_three_states("eventbridgeschedule.any.target",
                                 self._rows, "weekly-cleanup", "cleanup-fn")

    def test_eventbridgeschedule_iamrole_target_role(self):
        self.assert_three_states("eventbridgeschedule.iamrole.target-role",
                                 self._rows, "weekly-cleanup", "scheduler-role")

    def test_schedules_outside_the_default_group_are_covered(self):
        rows = self._rows()
        self.assertIn("batch", rows[0]["description"])

    def test_the_expression_and_timezone_are_recorded(self):
        rows = self._rows()
        self.assertEqual(rows[0]["service"], "EventBridgeSchedule")
        self.assertIn("cron(0 3 ? * MON *)", rows[0]["description"])
        self.assertIn("UTC", rows[0]["description"])

    def test_a_dead_letter_queue_is_linked(self):
        dlq = f"arn:aws:sqs:{REGION}:{ACCOUNT}:cleanup-dlq"
        rows = self._rows(dead_letter=dlq) + [make_row("SQSQueue", "cleanup-dlq")]
        audit.resolve_edges(rows)
        self.assertIn("dead-letters to",
                      audit.render_connections(self._row(rows, "weekly-cleanup")))


class LoggingConnectionTests(ConnectionTypeTestCase):

    def test_cloudtrail_loggroup_arn(self):
        def build():
            session = FakeSession({"cloudtrail": {
                "describe_trails": {"trailList": [{
                    "Name": "audit-trail", "HomeRegion": REGION,
                    "TrailARN": f"arn:aws:cloudtrail:{REGION}:{ACCOUNT}:trail/audit-trail",
                    "CloudWatchLogsLogGroupArn":
                        f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/cloudtrail/audit:*",
                    "IsMultiRegionTrail": False,
                }]},
                "get_trail_status": {"IsLogging": True, "LatestDeliveryTime": recent()},
            }})
            rows = audit.collect_cloudtrail_trails(session, REGION)
            return rows + [make_row("CloudWatchLogGroup", "/aws/cloudtrail/audit")]
        self.assert_three_states(
            "cloudtrail.loggroup.arn",
            build,
            f"arn:aws:cloudtrail:{REGION}:{ACCOUNT}:trail/audit-trail",
            "/aws/cloudtrail/audit",
        )

    @staticmethod
    def _log_group_session(name, filters=None):
        return FakeSession({"logs": {
            "describe_log_groups": {"logGroups": [{
                "logGroupName": name, "creationTime": epoch_ms(recent()), "storedBytes": 1024,
            }]},
            "describe_log_streams": {"logStreams": [{"lastEventTimestamp": epoch_ms(recent())}]},
            "describe_subscription_filters": {"subscriptionFilters": filters or []},
        }})

    def test_loggroup_name_convention(self):
        def build():
            rows = audit.collect_cloudwatch_log_groups(
                self._log_group_session("/aws/lambda/orders-fn"), REGION)
            return rows + [make_row("LambdaFunction", "orders-fn")]
        self.assert_three_states(
            "loggroup.any.name-convention", build, "/aws/lambda/orders-fn", "orders-fn")

    def test_an_aurora_cluster_log_export_links_to_its_cluster(self):
        rows = audit.collect_cloudwatch_log_groups(
            self._log_group_session("/aws/rds/cluster/orders-aurora/postgresql"), REGION)
        rows.append(make_row("RDSCluster", "orders-aurora"))
        audit.resolve_edges(rows)
        self.assertIn("RDSCluster:orders-aurora", audit.render_connections(rows[0]))

    def test_loggroup_subscription_filter(self):
        def build():
            rows = audit.collect_cloudwatch_log_groups(
                self._log_group_session("/custom/app", filters=[{
                    "destinationArn": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:shipper-fn"}]),
                REGION)
            return rows + [make_row("LambdaFunction", "shipper-fn")]
        self.assert_three_states(
            "loggroup.any.subscription-filter", build, "/custom/app", "shipper-fn")

    def test_unenforced_naming_conventions_never_become_edges(self):
        """CodeBuild/ECS/EKS naming is a convention, not enforced by AWS, so
        matching on it risks a false merge. It should be shown as text only."""
        for name in ("/aws/codebuild/my-project", "/ecs/my-service",
                     "/aws/vendedlogs/states/my-machine"):
            with self.subTest(name=name):
                rows = audit.collect_cloudwatch_log_groups(
                    self._log_group_session(name), REGION)
                self.assertEqual(
                    rows[0].get("_edges", []), [],
                    f"{name} produced a grouping edge from unenforced naming")

    def test_unrecognised_name_is_called_out_rather_than_guessed(self):
        rows = audit.collect_cloudwatch_log_groups(
            self._log_group_session("my-application-logs"), REGION)
        self.assertIn("custom", rows[0]["notes"].lower())


if __name__ == "__main__":
    unittest.main()
