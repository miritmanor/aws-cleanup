"""Connection types for Step Functions: what a state machine's definition names,
by ARN or by a table or bucket parameter, and the role it runs as."""

import json
import unittest

from .fakes import FakeSession, make_row, audit, recent
from .connections_base import ACCOUNT, ConnectionTypeTestCase, REGION


class StateMachineConnectionTests(ConnectionTypeTestCase):

    @staticmethod
    def _machine(definition, role=""):
        session = FakeSession({"stepfunctions": {
            "list_state_machines": {"stateMachines": [{
                "stateMachineArn": f"arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:orders",
                "name": "orders", "creationDate": recent()}]},
            "describe_state_machine": {"definition": json.dumps(definition), "roleArn": role},
            "list_executions": {"executions": []},
            "list_tags_for_resource": {"tags": []},
        }})
        return audit.collect_state_machines(session, REGION)

    def test_statemachine_any_definition_reference(self):
        fn = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:charge-card"
        definition = {"States": {"Charge": {
            "Type": "Task", "Resource": "arn:aws:states:::lambda:invoke",
            "Parameters": {"FunctionName": fn}}}}
        self.assert_three_states("statemachine.any.definition-reference",
                                 lambda: self._machine(definition)
                                 + [make_row("LambdaFunction", "charge-card")],
                                 "orders", "charge-card")

    def test_statemachine_iamrole_execution_role(self):
        role = f"arn:aws:iam::{ACCOUNT}:role/orders-sfn"
        self.assert_three_states("statemachine.iamrole.execution-role",
                                 lambda: self._machine({}, role)
                                 + [make_row("IAMRole", "orders-sfn", region="global")],
                                 "orders", "orders-sfn")

    def test_service_integration_arns_name_nothing(self):
        definition = {"States": {"A": {"Resource": "arn:aws:states:::sqs:sendMessage"}}}
        self.assertEqual(self._machine(definition)[0]["_edges"], [])

    def test_statemachine_dynamodb_table_parameter(self):
        definition = {"States": {"Save": {
            "Type": "Task", "Resource": "arn:aws:states:::dynamodb:putItem",
            "Parameters": {"TableName": "orders-live", "Item": {}}}}}
        self.assert_three_states("statemachine.dynamodb.table-parameter",
                                 lambda: self._machine(definition) + [make_row("DynamoDBTable", "orders-live")],
                                 "orders", "orders-live")

    def test_statemachine_s3bucket_bucket_parameter(self):
        """An SDK integration, inside a Map's item processor, with JSONata Arguments."""
        definition = {"States": {"Each": {"Type": "Map", "ItemProcessor": {"States": {"Put": {
            "Type": "Task", "Resource": "arn:aws:states:::aws-sdk:s3:putObject",
            "Arguments": {"Bucket": "receipts-store", "Key": "{% $states.input.key %}"}}}}}}}
        self.assert_three_states("statemachine.s3bucket.bucket-parameter",
                                 lambda: self._machine(definition)
                                 + [make_row("S3Bucket", "receipts-store", region="eu-west-1")],
                                 "orders", "receipts-store")

    def test_a_name_filled_in_at_run_time_links_nothing(self):
        definition = {"States": {
            "Path": {"Type": "Task", "Resource": "arn:aws:states:::dynamodb:getItem",
                     "Parameters": {"TableName.$": "$.table"}},
            "Jsonata": {"Type": "Task", "Resource": "arn:aws:states:::aws-sdk:s3:getObject",
                        "Arguments": {"Bucket": "{% $states.input.bucket %}"}}}}
        self.assertEqual(self._machine(definition)[0].get("_edges", []), [])

    def test_a_table_name_outside_a_dynamodb_task_links_nothing(self):
        definition = {"States": {"Call": {"Type": "Task", "Resource": "arn:aws:states:::lambda:invoke",
                                          "Parameters": {"FunctionName": "x", "Payload": {"TableName": "t"}}}}}
        self.assertNotIn("DynamoDBTable", [e["target_service"] for e in self._machine(definition)[0]["_edges"]])


if __name__ == "__main__":
    unittest.main()
