"""Connection types for streaming: what a Firehose delivery stream reads from,
writes to, transforms with and runs as. Each in all three states."""

import unittest

from .fakes import NO_METRICS, FakeSession, make_row, audit, recent
from .connections_base import ACCOUNT, ConnectionTypeTestCase, REGION


class FirehoseConnectionTests(ConnectionTypeTestCase):

    @staticmethod
    def _firehose(source=None, **s3):
        description = {"DeliveryStreamStatus": "ACTIVE", "CreateTimestamp": recent(),
                       "Destinations": [{"ExtendedS3DestinationDescription": s3}]}
        if source:
            description["Source"] = {"KinesisStreamSourceDescription": {"KinesisStreamARN": source}}
        session = FakeSession({"firehose": {
            "list_delivery_streams": {"DeliveryStreamNames": ["sink"], "HasMoreDeliveryStreams": False},
            "describe_delivery_stream": {"DeliveryStreamDescription": description},
            "list_tags_for_delivery_stream": {"Tags": []},
        }, "cloudwatch": NO_METRICS})
        return audit.collect_firehose_streams(session, REGION)

    def test_firehose_kinesis_source(self):
        arn = f"arn:aws:kinesis:{REGION}:{ACCOUNT}:stream/clicks"
        self.assert_three_states("firehose.kinesis.source",
                                 lambda: self._firehose(source=arn) + [make_row("KinesisStream", "clicks")],
                                 "sink", "clicks")

    def test_firehose_s3bucket_destination(self):
        self.assert_three_states("firehose.s3bucket.destination",
                                 lambda: self._firehose(BucketARN="arn:aws:s3:::click-lake")
                                 + [make_row("S3Bucket", "click-lake")], "sink", "click-lake")

    def test_firehose_iamrole_role(self):
        self.assert_three_states("firehose.iamrole.role",
                                 lambda: self._firehose(RoleARN=f"arn:aws:iam::{ACCOUNT}:role/sink-role")
                                 + [make_row("IAMRole", "sink-role", region="global")], "sink", "sink-role")

    def test_firehose_lambda_processor(self):
        processing = {"Processors": [{"Type": "Lambda", "Parameters": [{
            "ParameterName": "LambdaArn",
            "ParameterValue": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:enrich:$LATEST"}]}]}
        self.assert_three_states("firehose.lambda.processor",
                                 lambda: self._firehose(ProcessingConfiguration=processing)
                                 + [make_row("LambdaFunction", "enrich")], "sink", "enrich")

    def test_a_kinesis_stream_arn_names_its_row(self):
        self.assertEqual(audit.probable_resource_id_from_arn(f"arn:aws:kinesis:{REGION}:{ACCOUNT}:stream/clicks"),
                         ("clicks", "KinesisStream"))


class MskConnectionTests(ConnectionTypeTestCase):

    @staticmethod
    def _cluster(**nodes):
        session = FakeSession({"kafka": {"list_clusters_v2": {"ClusterInfoList": [{
            "ClusterName": "bus", "ClusterType": "PROVISIONED", "CreationTime": recent(),
            "Provisioned": {"NumberOfBrokerNodes": 0, "BrokerNodeGroupInfo": nodes}}]}},
            "cloudwatch": NO_METRICS})
        return audit.collect_msk_clusters(session, REGION)

    def test_msk_subnet_placement(self):
        self.assert_three_states("msk.subnet.placement",
                                 lambda: self._cluster(ClientSubnets=["subnet-0123"])
                                 + [make_row("Subnet", "subnet-0123")], "bus", "subnet-0123")

    def test_msk_securitygroup_membership(self):
        self.assert_three_states("msk.securitygroup.membership",
                                 lambda: self._cluster(SecurityGroups=["sg-0123"])
                                 + [make_row("SecurityGroup", "sg-0123")], "bus", "sg-0123")

    def test_an_msk_cluster_arn_names_its_row(self):
        arn = f"arn:aws:kafka:{REGION}:{ACCOUNT}:cluster/bus/1a2b3c-4"
        self.assertEqual(audit.probable_resource_id_from_arn(arn), ("bus", "MSKCluster"))


if __name__ == "__main__":
    unittest.main()
