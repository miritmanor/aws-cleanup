"""Connection types for AppSync: data sources and authorizers. Each in all
three states (connections_base)."""

import unittest

from .fakes import NO_METRICS, FakeSession, make_row, audit
from .connections_base import ACCOUNT, ConnectionTypeTestCase, REGION

FN_ARN = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:resolver"


class AppSyncConnectionTests(ConnectionTypeTestCase):

    @staticmethod
    def _api(sources=(), **api):
        session = FakeSession({"appsync": {
            "list_graphql_apis": {"graphqlApis": [dict({"apiId": "gql1", "name": "shop"}, **api)]},
            "list_data_sources": {"dataSources": list(sources)},
        }, "cloudwatch": NO_METRICS})
        return audit.collect_appsync_apis(session, REGION)

    def test_appsync_lambda_datasource(self):
        self.assert_three_states(
            "appsync.lambda.datasource",
            lambda: self._api([{"name": "fn", "type": "AWS_LAMBDA", "lambdaConfig": {"lambdaFunctionArn": FN_ARN}}])
            + [make_row("LambdaFunction", "resolver")], "gql1", "resolver")

    def test_appsync_dynamodb_datasource(self):
        self.assert_three_states(
            "appsync.dynamodb.datasource",
            lambda: self._api([{"name": "t", "type": "AMAZON_DYNAMODB", "dynamodbConfig": {"tableName": "carts"}}])
            + [make_row("DynamoDBTable", "carts")], "gql1", "carts")

    def test_appsync_cognito_authorizer(self):
        """An additional provider counts as much as the primary one."""
        pool = f"{REGION}_pool1"
        self.assert_three_states(
            "appsync.cognito.authorizer",
            lambda: self._api(authenticationType="API_KEY", additionalAuthenticationProviders=[
                {"authenticationType": "AMAZON_COGNITO_USER_POOLS", "userPoolConfig": {"userPoolId": pool}}])
            + [make_row("CognitoUserPool", pool)], "gql1", pool)

    def test_appsync_lambda_authorizer(self):
        self.assert_three_states(
            "appsync.lambda.authorizer",
            lambda: self._api(authenticationType="AWS_LAMBDA",
                              lambdaAuthorizerConfig={"authorizerUri": FN_ARN})
            + [make_row("LambdaFunction", "resolver")], "gql1", "resolver")


if __name__ == "__main__":
    unittest.main()
