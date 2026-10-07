"""Collector tests for streaming: Kinesis Data Streams and Data Firehose."""

import unittest

from .fakes import FakeSession, ancient, metrics_all_zero, metrics_at, audit, recent
from .collectors_base import REGION, only


class KinesisCollectorTests(unittest.TestCase):
    @staticmethod
    def _stream(cw, mode="PROVISIONED"):
        return FakeSession({"kinesis": {
            "list_streams": {"StreamNames": ["clicks"]},
            "describe_stream_summary": {"StreamDescriptionSummary": {
                "StreamName": "clicks", "StreamARN": "arn:clicks", "OpenShardCount": 2,
                "StreamModeDetails": {"StreamMode": mode}, "StreamCreationTimestamp": ancient(),
                "RetentionPeriodHours": 24}},
            "list_tags_for_stream": {"Tags": [{"Key": "Project", "Value": "web"}]},
        }, "cloudwatch": cw})

    def test_a_provisioned_stream_nobody_writes_to_still_bills_its_shards(self):
        row = only(audit.collect_kinesis_streams(self._stream(metrics_all_zero(recent())), REGION))
        self.assertEqual((row["service"], row["billing"]), ("KinesisStream", "cost"))
        self.assertNotIn("ACTIVE", row["flag"])
        self.assertIn("2 open shard(s)", row["notes"])
        self.assertEqual(row["tags"], {"Project": "web"})

    def test_an_on_demand_stream_with_records_is_active(self):
        row = only(audit.collect_kinesis_streams(self._stream(metrics_at(recent()), "ON_DEMAND"), REGION))
        self.assertIn("ACTIVE", row["flag"])
        self.assertIn("on_demand", row["description"])


class FirehoseCollectorTests(unittest.TestCase):
    def test_delivery_streams_page_by_the_last_name_seen(self):
        def names(Limit, ExclusiveStartDeliveryStreamName=None):
            if ExclusiveStartDeliveryStreamName == "a":
                return {"DeliveryStreamNames": ["b"], "HasMoreDeliveryStreams": False}
            return {"DeliveryStreamNames": ["a"], "HasMoreDeliveryStreams": True}
        session = FakeSession({"firehose": {
            "list_delivery_streams": names,
            "describe_delivery_stream": {"DeliveryStreamDescription": {
                "DeliveryStreamStatus": "ACTIVE", "DeliveryStreamType": "DirectPut",
                "CreateTimestamp": ancient(), "Destinations": []}},
            "list_tags_for_delivery_stream": {"Tags": []},
        }, "cloudwatch": metrics_all_zero(recent())})
        rows = audit.collect_firehose_streams(session, REGION)
        self.assertEqual([r["resource_id"] for r in rows], ["a", "b"])
        self.assertEqual({(r["service"], r["billing"]) for r in rows}, {("FirehoseStream", "usage")})


class MskCollectorTests(unittest.TestCase):
    def test_a_provisioned_cluster_is_active_when_any_broker_takes_messages(self):
        def metrics(**kwargs):
            broker = {d["Name"]: d["Value"] for d in kwargs["Dimensions"]}["Broker ID"]
            return (metrics_at(recent()) if broker == "2" else metrics_all_zero(recent()))["get_metric_statistics"]
        session = FakeSession({"kafka": {"list_clusters_v2": {"ClusterInfoList": [{
            "ClusterName": "bus", "ClusterType": "PROVISIONED", "CreationTime": ancient(),
            "Tags": {"Project": "web"},
            "Provisioned": {"NumberOfBrokerNodes": 3, "BrokerNodeGroupInfo": {
                "InstanceType": "kafka.m5.large", "ClientSubnets": ["subnet-a"]}}}]}},
            "cloudwatch": {"get_metric_statistics": metrics}})
        row = only(audit.collect_msk_clusters(session, REGION))
        self.assertEqual((row["service"], row["billing"]), ("MSKCluster", "cost"))
        self.assertIn("ACTIVE", row["flag"])
        self.assertEqual(len(session.get("cloudwatch").calls), 3)

    def test_a_serverless_cluster_is_unknown_and_asks_no_metric(self):
        session = FakeSession({"kafka": {"list_clusters_v2": {"ClusterInfoList": [{
            "ClusterName": "lite", "ClusterType": "SERVERLESS", "CreationTime": ancient(),
            "Serverless": {"VpcConfigs": [{"SubnetIds": ["subnet-a"]}]}}]}}})
        row = only(audit.collect_msk_clusters(session, REGION))
        self.assertIn("UNKNOWN (serverless", row["flag"])
        self.assertEqual(session.get("cloudwatch").calls, [])


if __name__ == "__main__":
    unittest.main()
