"""Connection types for API Gateway: Lambda integrations, authorizers (Cognito and
Lambda) and custom domain mappings. Each in all three states (connections_base)."""

import unittest
from .fakes import NO_METRICS, FakeSession, make_row, audit, recent
from .connections_base import ACCOUNT, ConnectionTypeTestCase, REGION


class ApiGatewayConnectionTests(ConnectionTypeTestCase):

    def test_apigateway_lambda_integration(self):
        def build():
            uri = (f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/"
                   f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:orders-fn/invocations")
            session = FakeSession({
                "apigateway": {
                    "get_authorizers": {"items": []},
                    "get_rest_apis": {"items": [{"id": "rest1", "name": "Orders API",
                                                 "createdDate": recent()}]},
                    "get_stages": {"item": [{"stageName": "prod"}]},
                    "get_resources": {"items": [{"id": "res1", "resourceMethods": {"GET": {}}}]},
                    "get_integration": {"type": "AWS_PROXY", "uri": uri},
                },
                "cloudwatch": NO_METRICS,
            })
            rows = audit.collect_api_gateway_rest_apis(session, REGION)
            return rows + [make_row("LambdaFunction", "orders-fn")]
        self.assert_three_states("apigateway.lambda.integration", build, "rest1", "orders-fn")

    def test_apigateway_lambda_stage_variable(self):
        """A ${stageVariables.X} placeholder in the URI is resolved per stage, not read literally."""
        def build():
            uri = (f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/"
                   f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:"
                   "${stageVariables.LAMBDA_FUNCTION}/invocations")
            session = FakeSession({
                "apigateway": {
                    "get_authorizers": {"items": []},
                    "get_rest_apis": {"items": [{"id": "rest1", "name": "Orders API",
                                                 "createdDate": recent()}]},
                    "get_stages": {"item": [{"stageName": "prod",
                                             "variables": {"LAMBDA_FUNCTION": "orders-fn"}}]},
                    "get_resources": {"items": [{"id": "res1", "resourceMethods": {"GET": {}}}]},
                    "get_integration": {"type": "AWS_PROXY", "uri": uri},
                },
                "cloudwatch": NO_METRICS,
            })
            rows = audit.collect_api_gateway_rest_apis(session, REGION)
            return rows + [make_row("LambdaFunction", "orders-fn")]
        self.assert_three_states("apigateway.lambda.stage-variable", build, "rest1", "orders-fn")

    def test_each_stage_resolves_its_own_backend(self):
        """dev and prod legitimately point at different functions, and both
        references are real."""
        self.assertEqual(
            sorted(audit.resolve_stage_variables(
                "function:${stageVariables.FN}/invocations",
                [{"FN": "orders-dev"}, {"FN": "orders-prod"}])),
            ["function:orders-dev/invocations", "function:orders-prod/invocations"])

    def test_unresolvable_stage_variable_is_left_dangling(self):
        """If no stage defines the variable we must not invent a target - the
        unresolved URI should still be reported as dangling."""
        self.assertEqual(
            audit.resolve_stage_variables("function:${stageVariables.FN}/invocations",
                                          [{"OTHER": "x"}]),
            [])

    def test_apigatewayv2_lambda_integration(self):
        def build():
            session = FakeSession({
                "apigatewayv2": {
                    "get_authorizers": {"Items": []},
                    "get_apis": {"Items": [{"ApiId": "http1", "Name": "Orders HTTP",
                                            "CreatedDate": recent(), "ProtocolType": "HTTP"}]},
                    "get_integrations": {"Items": [{
                        "IntegrationType": "AWS_PROXY",
                        "IntegrationUri": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:orders-fn",
                    }]},
                },
                "cloudwatch": NO_METRICS,
            })
            rows = audit.collect_api_gateway_v2_apis(session, REGION)
            return rows + [make_row("LambdaFunction", "orders-fn")]
        self.assert_three_states("apigatewayv2.lambda.integration", build, "http1", "orders-fn")

    def test_rest_api_pointing_at_a_deleted_lambda_is_dangling(self):
        """A REST API wired to a Lambda that no longer exists is evidence the
        API is abandoned - it must be surfaced, not silently counted as a link."""
        uri = (f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/"
               f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:deleted-fn/invocations")
        session = FakeSession({
            "apigateway": {
                "get_authorizers": {"items": []},
                "get_rest_apis": {"items": [{"id": "rest1", "name": "Ghost API",
                                             "createdDate": recent()}]},
                "get_stages": {"item": [{"stageName": "prod"}]},
                "get_resources": {"items": [{"id": "res1", "resourceMethods": {"GET": {}}}]},
                "get_integration": {"type": "AWS_PROXY", "uri": uri},
            },
            "cloudwatch": NO_METRICS,
        })
        rows = audit.collect_api_gateway_rest_apis(session, REGION)
        audit.resolve_edges(rows)
        self.assertIn("DANGLING", audit.render_connections(rows[0]))
        self.assertIn("deleted-fn", audit.render_connections(rows[0]))
        # Not LOW: absent from this scan is not gone (the lookup may have been denied).
        self.assertTrue(rows[0]["risk_if_removed"].startswith("UNKNOWN"),
                        "an unresolved target is missing evidence, not evidence of absence")
        reference = [r for r in rows[0]["_references"] if r["kind"] == "dangling"][0]
        self.assertEqual(reference["state"], "not_found_in_inventory")


class AuthorizerConnectionTests(ConnectionTypeTestCase):
    """What an API asks before it answers: a Cognito pool or a Lambda function."""

    POOL = f"{REGION}_pool1"
    AUTH_URI = (f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/"
                f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:gatekeeper/invocations")

    def _rest(self, authorizer):
        session = FakeSession({"apigateway": {
            "get_authorizers": {"items": [dict(authorizer, name="auth")]},
            "get_rest_apis": {"items": [{"id": "rest1", "name": "Orders API", "createdDate": recent()}]},
            "get_stages": {"item": []},
            "get_resources": {"items": []},
        }, "cloudwatch": NO_METRICS})
        return audit.collect_api_gateway_rest_apis(session, REGION)

    def _http(self, authorizer):
        session = FakeSession({"apigatewayv2": {
            "get_authorizers": {"Items": [dict(authorizer, Name="auth")]},
            "get_apis": {"Items": [{"ApiId": "http1", "Name": "Orders HTTP", "CreatedDate": recent()}]},
            "get_integrations": {"Items": []},
        }, "cloudwatch": NO_METRICS})
        return audit.collect_api_gateway_v2_apis(session, REGION)

    def test_apigateway_cognito_authorizer(self):
        arn = f"arn:aws:cognito-idp:{REGION}:{ACCOUNT}:userpool/{self.POOL}"
        self.assert_three_states(
            "apigateway.cognito.authorizer",
            lambda: self._rest({"type": "COGNITO_USER_POOLS", "providerARNs": [arn]})
            + [make_row("CognitoUserPool", self.POOL)], "rest1", self.POOL)

    def test_apigateway_lambda_authorizer(self):
        self.assert_three_states(
            "apigateway.lambda.authorizer",
            lambda: self._rest({"type": "TOKEN", "authorizerUri": self.AUTH_URI})
            + [make_row("LambdaFunction", "gatekeeper")], "rest1", "gatekeeper")

    def test_apigatewayv2_cognito_authorizer(self):
        issuer = f"https://cognito-idp.{REGION}.amazonaws.com/{self.POOL}"
        self.assert_three_states(
            "apigatewayv2.cognito.authorizer",
            lambda: self._http({"AuthorizerType": "JWT", "JwtConfiguration": {"Issuer": issuer}})
            + [make_row("CognitoUserPool", self.POOL)], "http1", self.POOL)

    def test_apigatewayv2_lambda_authorizer(self):
        self.assert_three_states(
            "apigatewayv2.lambda.authorizer",
            lambda: self._http({"AuthorizerType": "REQUEST", "AuthorizerUri": self.AUTH_URI})
            + [make_row("LambdaFunction", "gatekeeper")], "http1", "gatekeeper")

    def test_a_jwt_issuer_that_is_not_cognito_links_nothing(self):
        rows = self._http({"AuthorizerType": "JWT",
                           "JwtConfiguration": {"Issuer": "https://login.example.com/tenant"}})
        self.assertEqual(rows[0].get("_edges", []), [])


class ApiDomainConnectionTests(ConnectionTypeTestCase):

    @staticmethod
    def _domain(base_paths=(), api_mappings=()):
        session = FakeSession({
            "apigateway": {
                "get_domain_names": {"items": [{"domainName": "api.shop.example",
                                                "endpointConfiguration": {"types": ["REGIONAL"]}}]},
                "get_base_path_mappings": {"items": list(base_paths)},
            },
            "apigatewayv2": {"get_api_mappings": {"Items": list(api_mappings)}},
        })
        return audit.collect_api_gateway_domain_names(session, REGION)

    def test_apidomain_apigateway_mapping(self):
        self.assert_three_states(
            "apidomain.apigateway.mapping",
            lambda: self._domain(base_paths=[{"basePath": "(none)", "restApiId": "rest1"}])
            + [make_row("APIGatewayRestApi", "rest1")], "api.shop.example", "rest1")

    def test_apidomain_apigatewayv2_mapping(self):
        self.assert_three_states(
            "apidomain.apigatewayv2.mapping",
            lambda: self._domain(api_mappings=[{"ApiId": "http1"}])
            + [make_row("APIGatewayV2Api", "http1")], "api.shop.example", "http1")


if __name__ == "__main__":
    unittest.main()
