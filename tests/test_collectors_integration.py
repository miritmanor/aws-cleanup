"""Collector tests for integration and observability: SQS, SNS, API Gateway,
CloudTrail, CloudWatch Logs and alarms, X-Ray."""

import unittest

from .fakes import NO_METRICS, FakeSession, ancient, metrics_at, audit, recent
from .collectors_base import (
    ACCOUNT,
    REGION,
    by_service,
    epoch_ms,
    only,
)


class IntegrationCollectorTests(unittest.TestCase):

    def test_sqs_queue(self):
        session = FakeSession({
            "sqs": {
                "list_queues": {"QueueUrls": [
                    f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/orders-queue"]},
                "get_queue_attributes": {"Attributes": {
                    "QueueArn": f"arn:aws:sqs:{REGION}:{ACCOUNT}:orders-queue",
                    "CreatedTimestamp": str(int(recent(90).timestamp())),
                    "LastModifiedTimestamp": str(int(recent(10).timestamp())),
                    "ApproximateNumberOfMessages": "0",
                }},
                "list_queue_tags": {"Tags": {"Project": "orders"}},
            },
            "cloudwatch": metrics_at(recent()),
        })
        row = only(audit.collect_sqs_queues(session, REGION))
        self.assertEqual(row["service"], "SQSQueue")
        self.assertEqual(row["resource_id"], "orders-queue")
        self.assertEqual(row["billing"], "usage")
        self.assertEqual(row["tags"]["Project"], "orders")
        self.assertIn("discards every message", row["notes"])

    def test_sqs_queue_with_undrained_backlog_and_no_traffic(self):
        """Undrained messages with nothing sending: the consumer is gone."""
        session = FakeSession({
            "sqs": {
                "list_queues": {"QueueUrls": [
                    f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/stuck-queue"]},
                "get_queue_attributes": {"Attributes": {
                    "CreatedTimestamp": str(int(ancient().timestamp())),
                    "ApproximateNumberOfMessages": "4200",
                    "ApproximateNumberOfMessagesNotVisible": "3",
                }},
                "list_queue_tags": {"Tags": {}},
            },
            "cloudwatch": NO_METRICS,
        })
        row = only(audit.collect_sqs_queues(session, REGION))
        self.assertEqual(row["flag"], "STALE (UNDRAINED BACKLOG - consumer likely gone)")
        self.assertIn("consumer is gone", row["notes"])

    def test_sqs_fifo_queue_is_identified(self):
        session = FakeSession({
            "sqs": {
                "list_queues": {"QueueUrls": [
                    f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/orders.fifo"]},
                "get_queue_attributes": {"Attributes": {
                    "FifoQueue": "true",
                    "CreatedTimestamp": str(int(recent(30).timestamp())),
                    "ApproximateNumberOfMessages": "0",
                }},
                "list_queue_tags": {"Tags": {}},
            },
            "cloudwatch": NO_METRICS,
        })
        row = only(audit.collect_sqs_queues(session, REGION))
        self.assertIn("FIFO", row["description"])

    def test_sns_topic_with_no_subscriptions(self):
        session = FakeSession({
            "sns": {
                "list_topics": {"Topics": [
                    {"TopicArn": f"arn:aws:sns:{REGION}:{ACCOUNT}:orphan-topic"}]},
                "get_topic_attributes": {"Attributes": {
                    "SubscriptionsConfirmed": "0", "SubscriptionsPending": "0"}},
                "list_tags_for_resource": {"Tags": []},
                "list_subscriptions_by_topic": {"Subscriptions": []},
            },
            "cloudwatch": NO_METRICS,
        })
        row = only(audit.collect_sns_topics(session, REGION))
        self.assertEqual(row["service"], "SNSTopic")
        self.assertEqual(row["resource_id"], "orphan-topic")
        self.assertIn("NO SUBSCRIPTIONS", row["flag"])
        self.assertEqual(row["billing"], "usage")

    def test_sns_topic_has_no_creation_date_so_silence_means_unknown(self):
        """SNS has no creation timestamp, so silence must read UNKNOWN, never old."""
        session = FakeSession({
            "sns": {
                "list_topics": {"Topics": [
                    {"TopicArn": f"arn:aws:sns:{REGION}:{ACCOUNT}:quiet-topic"}]},
                "get_topic_attributes": {"Attributes": {"SubscriptionsConfirmed": "2"}},
                "list_tags_for_resource": {"Tags": []},
                "list_subscriptions_by_topic": {"Subscriptions": [
                    {"Protocol": "email", "Endpoint": "a@example.com"},
                    {"Protocol": "email", "Endpoint": "b@example.com"}]},
            },
            "cloudwatch": NO_METRICS,
        })
        row = only(audit.collect_sns_topics(session, REGION))
        self.assertEqual(row["flag"], "UNKNOWN")
        self.assertEqual(row["created"], "")
        self.assertIn("no creation timestamp", row["notes"])

    def test_api_gateway_rest_api(self):
        session = FakeSession({
            "apigateway": {
                "get_authorizers": {"items": []},
                "get_rest_apis": {"items": [{"id": "rest1", "name": "Orders API",
                                             "createdDate": recent(), "description": "v1"}]},
                "get_stages": {"item": [{"stageName": "prod"}]},
                "get_resources": {"items": []},
            },
            "cloudwatch": metrics_at(recent()),
        })
        row = only(audit.collect_api_gateway_rest_apis(session, REGION))
        self.assertEqual(row["service"], "APIGatewayRestApi")
        self.assertEqual(row["billing"], "usage")

    def test_api_gateway_v2_api(self):
        session = FakeSession({
            "apigatewayv2": {
                "get_authorizers": {"Items": []},
                "get_apis": {"Items": [{"ApiId": "http1", "Name": "Orders HTTP",
                                        "CreatedDate": recent(), "ProtocolType": "HTTP"}]},
                "get_integrations": {"Items": []},
            },
            "cloudwatch": metrics_at(recent()),
        })
        row = only(audit.collect_api_gateway_v2_apis(session, REGION))
        self.assertEqual(row["service"], "APIGatewayV2Api")

    @staticmethod
    def _domains(base_paths, api_mappings, types=("REGIONAL",)):
        return FakeSession({
            "apigateway": {
                "get_domain_names": {"items": [{
                    "domainName": "api.shop.example", "endpointConfiguration": {"types": list(types)},
                    "regionalDomainName": "d-abc123.execute-api.us-east-1.amazonaws.com"}]},
                "get_base_path_mappings": {"items": base_paths},
            },
            "apigatewayv2": {"get_api_mappings": {"Items": api_mappings}},
        })

    def test_a_custom_domain_mapped_to_nothing_is_stale(self):
        row = only(audit.collect_api_gateway_domain_names(self._domains([], []), REGION))
        self.assertEqual((row["service"], row["billing"]), ("APIGatewayDomainName", "free"))
        self.assertIn("STALE", row["flag"])
        self.assertIn("d-abc123.execute-api", row["description"])

    def test_a_custom_domain_counts_each_api_once(self):
        session = self._domains([{"basePath": "v1", "restApiId": "rest1"}],
                                [{"ApiId": "rest1", "ApiMappingKey": "v1"}, {"ApiId": "http1"}])
        row = only(audit.collect_api_gateway_domain_names(session, REGION))
        self.assertIn("maps to 2 API(s)", row["flag"])
        self.assertEqual(sorted((e["target_id"], e["target_service"]) for e in row["_edges"]),
                         [("http1", "APIGatewayV2Api"), ("rest1", "APIGatewayRestApi")])

    def test_an_edge_optimized_domain_does_not_ask_the_v2_api(self):
        session = self._domains([], [], types=("EDGE",))
        audit.collect_api_gateway_domain_names(session, REGION)
        self.assertEqual(session.get("apigatewayv2").operations_called(), [])

    def test_an_appsync_api_counts_its_requests_from_latency_samples(self):
        session = FakeSession({"appsync": {
            "list_graphql_apis": {"graphqlApis": [{"apiId": "gql1", "name": "shop", "tags": {"Project": "web"}}]},
            "list_data_sources": {"dataSources": []},
        }, "cloudwatch": metrics_at(recent(), stat="SampleCount")})
        row = only(audit.collect_appsync_apis(session, REGION))
        self.assertEqual((row["service"], row["billing"], row["tags"]), ("AppSyncApi", "usage", {"Project": "web"}))
        self.assertIn("ACTIVE", row["flag"])
        (_op, kwargs), = session.get("cloudwatch").calls
        self.assertEqual((kwargs["MetricName"], kwargs["Statistics"]), ("Latency", ["SampleCount"]))

    def test_cloudtrail_trail_not_logging(self):
        session = FakeSession({"cloudtrail": {
            "describe_trails": {"trailList": [{
                "Name": "audit", "HomeRegion": REGION,
                "TrailARN": f"arn:aws:cloudtrail:{REGION}:{ACCOUNT}:trail/audit",
                "IsMultiRegionTrail": True,
            }]},
            "get_trail_status": {"IsLogging": False},
        }})
        row = only(audit.collect_cloudtrail_trails(session, REGION))
        self.assertEqual(row["service"], "CloudTrailTrail")
        self.assertIn("NOT LOGGING", row["flag"])

    def test_multi_region_trail_is_reported_once_from_its_home_region(self):
        """Multi-region trails appear in every region's DescribeTrails; without
        the home-region filter they'd be counted once per region."""
        session = FakeSession({"cloudtrail": {
            "describe_trails": {"trailList": [{
                "Name": "audit", "HomeRegion": "us-west-2",
                "TrailARN": "arn:aws:cloudtrail:us-west-2:111122223333:trail/audit",
            }]},
            "get_trail_status": {"IsLogging": True},
        }})
        self.assertEqual(audit.collect_cloudtrail_trails(session, REGION), [])

    def test_cloudwatch_log_group(self):
        session = FakeSession({"logs": {
            "describe_log_groups": {"logGroups": [{
                "logGroupName": "/aws/lambda/orders-fn", "creationTime": epoch_ms(recent()),
                "storedBytes": 4096, "retentionInDays": 30,
            }]},
            "describe_log_streams": {"logStreams": [{"lastEventTimestamp": epoch_ms(recent())}]},
            "describe_subscription_filters": {"subscriptionFilters": []},
        }})
        row = only(audit.collect_cloudwatch_log_groups(session, REGION))
        self.assertEqual(row["service"], "CloudWatchLogGroup")
        self.assertEqual(row["billing"], "cost")
        self.assertIn("4096", row["description"])

    def test_cloudwatch_alarm_insufficient_data(self):
        session = FakeSession({"cloudwatch": {"describe_alarms": {"MetricAlarms": [{
            "AlarmName": "cpu-high", "AlarmArn": f"arn:aws:cloudwatch:{REGION}:{ACCOUNT}:alarm:cpu-high",
            "StateValue": "INSUFFICIENT_DATA", "StateUpdatedTimestamp": recent(),
            "AlarmDescription": "pages oncall when the bakery worker fleet is pegged",
        }]}}})
        row = only(audit.collect_cloudwatch_alarms(session, REGION))
        self.assertEqual(row["service"], "CloudWatchAlarm")
        self.assertIn("INSUFFICIENT_DATA", row["flag"])
        self.assertEqual(row["description"], "pages oncall when the bakery worker fleet is pegged")

    def test_the_xray_default_group_is_not_listed(self):
        """AWS creates one in every region. It is not something the account made."""
        session = FakeSession({"xray": {
            "get_groups": {"Groups": [
                {"GroupName": "Default",
                 "GroupARN": f"arn:aws:xray:{REGION}:{ACCOUNT}:group/Default"}]},
            "get_sampling_rules": {"SamplingRuleRecords": [
                {"SamplingRule": {"RuleName": "Default"}}]},
        }})
        self.assertEqual(audit.collect_xray_config(session, REGION), [])

    def test_xray_group_and_sampling_rule(self):
        session = FakeSession({"xray": {
            "get_groups": {"Groups": [{"GroupName": "orders",
                                       "GroupARN": f"arn:aws:xray:{REGION}:{ACCOUNT}:group/orders"}]},
            "get_sampling_rules": {"SamplingRuleRecords": [
                {"SamplingRule": {"RuleName": "Default", "Priority": 10000}},
                {"SamplingRule": {"RuleName": "orders-rule", "Priority": 100,
                                  "RuleARN": f"arn:aws:xray:{REGION}:{ACCOUNT}:sampling-rule/orders-rule"},
                 "CreatedAt": recent(200), "ModifiedAt": recent(10)},
            ]},
        }})
        rows = audit.collect_xray_config(session, REGION)
        self.assertEqual(len(by_service(rows, "XRayGroup")), 1)
        rules = by_service(rows, "XRaySamplingRule")
        self.assertEqual(len(rules), 1, "AWS's built-in Default rule is not a user resource")
        self.assertEqual(rules[0]["name"], "orders-rule")


if __name__ == "__main__":
    unittest.main()
