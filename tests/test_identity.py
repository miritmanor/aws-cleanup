"""A reference resolves to the resource it names, or to nothing: scoped by region and
account, never by bare id or name alone."""

import unittest

from .fakes import FakeSession, audit, make_row  # noqa: F401

EAST = "us-east-1"
WEST = "us-west-2"
ACCOUNT = "111122223333"
OTHER_ACCOUNT = "999988887777"


def table_arn(name, region=EAST, account=ACCOUNT):
    return f"arn:aws:dynamodb:{region}:{account}:table/{name}"


def resolved_targets(row):
    """resource_ids this row resolved to, from its written references."""
    return [r["target_id"] for r in row["_references"] if r["kind"] == "resolved"]


def reference_kinds(row):
    return [r["kind"] for r in row["_references"]]


class ResourceKeyTests(unittest.TestCase):
    """The canonical key: partition, service, account, scope, native id."""

    def test_key_carries_account_and_region(self):
        row = make_row("DynamoDBTable", "orders", region=EAST, account=ACCOUNT)
        self.assertEqual(row["resource_key"],
                         f"aws:DynamoDBTable:{ACCOUNT}:{EAST}:orders")

    def test_same_name_in_two_regions_gets_two_keys(self):
        east = make_row("DynamoDBTable", "orders", region=EAST, account=ACCOUNT)
        west = make_row("DynamoDBTable", "orders", region=WEST, account=ACCOUNT)
        self.assertNotEqual(east["resource_key"], west["resource_key"])

    def test_global_resources_say_global(self):
        row = make_row("S3Bucket", "assets", region="global", account=ACCOUNT)
        self.assertEqual(row["resource_key"],
                         f"aws:S3Bucket:{ACCOUNT}:global:assets")

    def test_display_name_is_not_part_of_the_key(self):
        a = make_row("KeyPair", "kp-1", name="Production key", account=ACCOUNT)
        b = make_row("KeyPair", "kp-1", name="Something else", account=ACCOUNT)
        self.assertEqual(a["resource_key"], b["resource_key"])

    def test_arn_supplies_account_when_the_scan_scope_does_not(self):
        row = make_row("DynamoDBTable", "orders", region=EAST,
                       arn=table_arn("orders", account=OTHER_ACCOUNT))
        self.assertEqual(row["account"], OTHER_ACCOUNT)


class CollectorsRecordTheArnTests(unittest.TestCase):
    """Collectors fill the identity fields (ARNs), so exact-ARN matching can fire."""

    def test_dynamodb_records_its_table_arn(self):
        arn = f"arn:aws:dynamodb:{EAST}:{ACCOUNT}:table/orders"
        session = FakeSession({
            "dynamodb": {
                "list_tables": {"TableNames": ["orders"]},
                "describe_table": {"Table": {
                    "TableName": "orders", "TableArn": arn,
                    "CreationDateTime": audit.now(),
                }},
                "list_tags_of_resource": {"Tags": []},
            },
            "cloudwatch": {"get_metric_statistics": {"Datapoints": []}},
        })
        row = audit.collect_dynamodb_tables(session, EAST)[0]

        self.assertEqual(row["arn"], arn)
        self.assertEqual(row["account"], ACCOUNT)
        self.assertEqual(row["resource_key"],
                         f"aws:DynamoDBTable:{ACCOUNT}:{EAST}:orders")

    def test_an_arn_from_another_account_sets_that_account(self):
        """A shared resource keeps its owner's account, not the scanner's."""
        arn = f"arn:aws:dynamodb:{EAST}:{OTHER_ACCOUNT}:table/shared"
        session = FakeSession({
            "dynamodb": {
                "list_tables": {"TableNames": ["shared"]},
                "describe_table": {"Table": {
                    "TableName": "shared", "TableArn": arn,
                    "CreationDateTime": audit.now(),
                }},
                "list_tags_of_resource": {"Tags": []},
            },
            "cloudwatch": {"get_metric_statistics": {"Datapoints": []}},
        })
        row = audit.collect_dynamodb_tables(session, EAST)[0]

        self.assertEqual(row["account"], OTHER_ACCOUNT)


class ExplicitArnScopeTests(unittest.TestCase):
    """An ARN names one resource. Resolution must honour every part of it."""

    def test_east_arn_does_not_match_the_west_table(self):
        fn = make_row("LambdaFunction", "sync", region=EAST, account=ACCOUNT)
        east = make_row("DynamoDBTable", "orders", region=EAST, account=ACCOUNT,
                        arn=table_arn("orders", EAST))
        west = make_row("DynamoDBTable", "orders", region=WEST, account=ACCOUNT,
                        arn=table_arn("orders", WEST))
        audit.add_edge(fn, "orders", "reads", "env var TABLE_ARN",
                       conn_type="lambda.any.env-var-arn-value",
                       target_service="DynamoDBTable",
                       target_arn=table_arn("orders", EAST),
                       target_region=EAST, target_account=ACCOUNT)

        audit.resolve_edges([fn, east, west])

        self.assertEqual(resolved_targets(fn), ["orders"])
        self.assertTrue(east["_references"], "the east table is the target")
        self.assertEqual(west["_references"], [],
                         "the west table must not be linked at all")

    def test_other_account_reference_does_not_resolve_locally(self):
        fn = make_row("LambdaFunction", "sync", region=EAST, account=ACCOUNT)
        local = make_row("DynamoDBTable", "orders", region=EAST, account=ACCOUNT,
                         arn=table_arn("orders"))
        audit.add_edge(fn, "orders", "reads", "env var TABLE_ARN",
                       conn_type="lambda.any.env-var-arn-value",
                       target_service="DynamoDBTable",
                       target_arn=table_arn("orders", EAST, OTHER_ACCOUNT),
                       target_region=EAST, target_account=OTHER_ACCOUNT)

        audit.resolve_edges([fn, local])

        self.assertEqual(resolved_targets(fn), [])
        self.assertEqual(local["_references"], [])


class BareNameScopeRuleTests(unittest.TestCase):
    """A bare name has no scope of its own, so the relationship supplies one."""

    def test_key_pair_resolves_in_the_instances_region(self):
        inst = make_row("EC2Instance", "i-1", region=EAST, account=ACCOUNT)
        here = make_row("KeyPair", "deploy", region=EAST, account=ACCOUNT)
        there = make_row("KeyPair", "deploy", region=WEST, account=ACCOUNT)
        audit.add_edge(inst, "deploy", "uses key pair", "KeyName",
                       conn_type="ec2.keypair.key-name",
                       target_service="KeyPair")

        audit.resolve_edges([inst, here, there])

        self.assertEqual(resolved_targets(inst), ["deploy"])
        self.assertTrue(here["_references"], "same-region key pair links")
        self.assertEqual(there["_references"], [],
                         "a key pair in another region is a different key pair")

    def test_iam_role_resolves_account_wide(self):
        """IAM is global, so the role row's region never matches the source's."""
        pool = make_row("CognitoUserPool", "pool-1", region=EAST, account=ACCOUNT)
        role = make_row("IAMRole", "sms-caller", region="global", account=ACCOUNT)
        audit.add_edge(pool, "sms-caller", "sends SMS as", "SmsConfiguration",
                       conn_type="cognito.iamrole.sms-caller",
                       target_service="IAMRole")

        audit.resolve_edges([pool, role])

        self.assertEqual(resolved_targets(pool), ["sms-caller"])


class AmbiguityTests(unittest.TestCase):
    """Several valid matches and no way to choose is not a link."""

    def test_ambiguous_bare_name_does_not_link_or_group(self):
        fn = make_row("LambdaFunction", "sync", region=EAST, account=ACCOUNT)
        east = make_row("DynamoDBTable", "orders", region=EAST, account=ACCOUNT)
        west = make_row("DynamoDBTable", "orders", region=WEST, account=ACCOUNT)
        audit.add_edge(fn, "orders", "may use", "env var TABLE_NAME",
                       conn_type="lambda.any.env-var-bare-name",
                       target_service="DynamoDBTable")

        links = audit.resolve_edges([fn, east, west])

        self.assertIn("ambiguous", reference_kinds(fn))
        self.assertEqual(resolved_targets(fn), [])
        self.assertEqual(links, [], "an ambiguous reference cannot group")

    def test_ambiguous_reference_records_its_candidates(self):
        fn = make_row("LambdaFunction", "sync", region=EAST, account=ACCOUNT)
        east = make_row("DynamoDBTable", "orders", region=EAST, account=ACCOUNT)
        west = make_row("DynamoDBTable", "orders", region=WEST, account=ACCOUNT)
        audit.add_edge(fn, "orders", "may use", "env var TABLE_NAME",
                       conn_type="lambda.any.env-var-bare-name",
                       target_service="DynamoDBTable")

        audit.resolve_edges([fn, east, west])

        ambiguous = [r for r in fn["_references"] if r["kind"] == "ambiguous"][0]
        self.assertCountEqual(ambiguous["candidates"],
                              [east["resource_key"], west["resource_key"]])


class PreservedMeaningTests(unittest.TestCase):
    """Qualifiers and deliberate wildcards keep saying what they said."""

    def test_a_collector_records_the_alias_an_integration_pins(self):
        """An API wired to a Lambda alias records the qualifier."""
        uri = (f"arn:aws:apigateway:{EAST}:lambda:path/2015-03-31/functions/"
               f"arn:aws:lambda:{EAST}:{ACCOUNT}:function:orders-fn:prod/invocations")
        session = FakeSession({
            "apigateway": {
                "get_authorizers": {"items": []},
                "get_rest_apis": {"items": [{"id": "rest1", "name": "API",
                                             "createdDate": audit.now()}]},
                "get_stages": {"item": [{"stageName": "prod"}]},
                "get_resources": {"items": [{"id": "res1",
                                             "resourceMethods": {"GET": {}}}]},
                "get_integration": {"type": "AWS_PROXY", "uri": uri},
            },
            "cloudwatch": {"get_metric_statistics": {"Datapoints": []}},
        })
        api = audit.collect_api_gateway_rest_apis(session, EAST)[0]

        edge = [e for e in api["_edges"]
                if e["conn_type"] == "apigateway.lambda.integration"][0]
        self.assertEqual(edge["target_id"], "orders-fn")
        self.assertEqual(edge["target_qualifier"], "prod")

    def test_lambda_qualifier_is_kept_as_evidence(self):
        api = make_row("APIGatewayRestApi", "api-1", region=EAST, account=ACCOUNT)
        fn = make_row("LambdaFunction", "handler", region=EAST, account=ACCOUNT)
        audit.add_edge(api, "handler", "routes to", "integration URI",
                       conn_type="apigateway.lambda.integration",
                       target_service="LambdaFunction",
                       target_qualifier="prod")

        audit.resolve_edges([api, fn])

        ref = [r for r in api["_references"] if r["kind"] == "resolved"][0]
        self.assertEqual(ref["target_qualifier"], "prod")

    def test_wildcard_region_grant_reaches_every_region(self):
        """arn:aws:dynamodb:*:*:table/orders really does grant both tables."""
        role = make_row("IAMRole", "reader", region="global", account=ACCOUNT)
        east = make_row("DynamoDBTable", "orders", region=EAST, account=ACCOUNT)
        west = make_row("DynamoDBTable", "orders", region=WEST, account=ACCOUNT)
        audit.add_edge(role, "orders", "grants access to", "inline policy",
                       conn_type="iamrole.any.resource-grant",
                       target_service="DynamoDBTable",
                       target_arn="arn:aws:dynamodb:*:*:table/orders",
                       target_region="*", target_account="*")

        audit.resolve_edges([role, east, west])

        self.assertEqual(sorted(resolved_targets(role)), ["orders", "orders"])


if __name__ == "__main__":
    unittest.main()
