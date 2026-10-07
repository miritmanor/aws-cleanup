"""Connection types for identity: IAM, roles a resource runs as, Cognito,
Secrets Manager."""

import unittest

from .fakes import FakeSession, find_edge, make_row, audit, recent
from .connections_base import (
    ACCOUNT,
    ConnectionTypeTestCase,
    REGION,
    cognito_session,
    ec2_session,
    function_payload,
    iam_session,
    instance_payload,
    lambda_session,
    quiet,
)


class IamConnectionTests(ConnectionTypeTestCase):

    POLICY_ARN = f"arn:aws:iam::{ACCOUNT}:policy/OrdersAccess"

    def _policy_session(self, entities, document=None):
        return iam_session(
            policies=[{
                "PolicyName": "OrdersAccess", "Arn": self.POLICY_ARN,
                "AttachmentCount": 1, "DefaultVersionId": "v1",
                "CreateDate": recent(), "UpdateDate": recent(),
            }],
            extra={
                "list_entities_for_policy": entities,
                "get_policy_version": {"PolicyVersion": {"Document": document or {
                    "Statement": [{"Effect": "Allow", "Action": "s3:*", "Resource": "*"}]}}},
            },
        )

    def test_iampolicy_iamrole_attachment(self):
        def build():
            session = self._policy_session({"PolicyRoles": [{"RoleName": "orders-role"}]})
            rows = audit.collect_iam(session)
            return rows + [make_row("IAMRole", "orders-role", region="global")]
        self.assert_three_states(
            "iampolicy.iamrole.attachment", build, self.POLICY_ARN, "orders-role")

    def test_iampolicy_iamuser_attachment(self):
        def build():
            session = self._policy_session({"PolicyUsers": [{"UserName": "deploy-user"}]})
            rows = audit.collect_iam(session)
            return rows + [make_row("IAMUser", "deploy-user", region="global")]
        self.assert_three_states(
            "iampolicy.iamuser.attachment", build, self.POLICY_ARN, "deploy-user")

    def test_iampolicy_iamgroup_attachment(self):
        def build():
            session = self._policy_session({"PolicyGroups": [{"GroupName": "developers"}]})
            rows = audit.collect_iam(session)
            return rows + [make_row("IAMGroup", "developers", region="global")]
        self.assert_three_states(
            "iampolicy.iamgroup.attachment", build, self.POLICY_ARN, "developers")

    def test_iampolicy_resource_grant(self):
        def build():
            session = self._policy_session(
                {"PolicyRoles": []},
                document={"Statement": [{
                    "Effect": "Allow", "Action": "dynamodb:GetItem",
                    "Resource": f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/orders",
                }]})
            rows = audit.collect_iam(session)
            return rows + [make_row("DynamoDBTable", "orders")]
        self.assert_three_states(
            "iampolicy.any.resource-grant", build, self.POLICY_ARN, "orders")

    def test_iamrole_resource_grant(self):
        def build():
            session = iam_session(
                roles=[{
                    "RoleName": "orders-role", "Path": "/", "CreateDate": recent(),
                    "RoleLastUsed": {"LastUsedDate": recent()},
                    "AssumeRolePolicyDocument": {"Statement": [{
                        "Effect": "Allow",
                        "Principal": {"Service": "lambda.amazonaws.com"},
                    }]},
                }],
                extra={
                    "list_role_policies": {"PolicyNames": ["inline-orders"]},
                    "get_role_policy": {"PolicyDocument": {"Statement": [{
                        "Effect": "Allow", "Action": "dynamodb:*",
                        "Resource": f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/orders",
                    }]}},
                },
            )
            rows = audit.collect_iam(session)
            return rows + [make_row("DynamoDBTable", "orders")]
        self.assert_three_states(
            "iamrole.any.resource-grant", build, "orders-role", "orders")

    def _grant_role_session(self, resource):
        """A role whose one inline policy grants `resource`."""
        return iam_session(
            roles=[{
                "RoleName": "orders-role", "Path": "/", "CreateDate": recent(),
                "RoleLastUsed": {"LastUsedDate": recent()},
                "AssumeRolePolicyDocument": {"Statement": [{
                    "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
                }]},
            }],
            extra={
                "list_role_policies": {"PolicyNames": ["inline-orders"]},
                "get_role_policy": {"PolicyDocument": {"Statement": [{
                    "Effect": "Allow", "Action": "dynamodb:*", "Resource": resource,
                }]}},
            },
        )

    def test_iampolicy_resource_grant_prefix(self):
        def build():
            session = self._policy_session(
                {"PolicyRoles": []},
                document={"Statement": [{
                    "Effect": "Allow", "Action": "dynamodb:GetItem",
                    "Resource": f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/orders*",
                }]})
            rows = audit.collect_iam(session)
            return rows + [make_row("DynamoDBTable", "orders")]
        self.assert_three_states(
            "iampolicy.any.resource-grant-prefix", build, self.POLICY_ARN, "orders")

    def test_iamrole_resource_grant_prefix(self):
        def build():
            session = self._grant_role_session(
                f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/orders*")
            rows = audit.collect_iam(session)
            return rows + [make_row("DynamoDBTable", "orders")]
        self.assert_three_states(
            "iamrole.any.resource-grant-prefix", build, "orders-role", "orders")

    def test_iampolicy_execute_api_grant(self):
        def build():
            session = self._policy_session(
                {"PolicyRoles": []},
                document={"Statement": [{
                    "Effect": "Allow", "Action": "execute-api:Invoke",
                    "Resource": f"arn:aws:execute-api:{REGION}:{ACCOUNT}:rest1/*/*/*",
                }]})
            rows = audit.collect_iam(session)
            return rows + [make_row("APIGatewayRestApi", "rest1")]
        self.assert_three_states(
            "iampolicy.apigateway.execute-api-grant", build, self.POLICY_ARN, "rest1")

    def test_iamrole_execute_api_grant(self):
        def build():
            session = self._grant_role_session(
                f"arn:aws:execute-api:{REGION}:{ACCOUNT}:rest1/prod/GET/items")
            rows = audit.collect_iam(session)
            return rows + [make_row("APIGatewayRestApi", "rest1")]
        self.assert_three_states(
            "iamrole.apigateway.execute-api-grant", build, "orders-role", "rest1")

    def test_execute_api_grant_is_the_only_thing_pointing_at_an_api(self):
        """The one mechanism that makes an API a link target (IAM execute-api grants)."""
        with self._isolated("iamrole.apigateway.execute-api-grant", audit.CONN_ON):
            session = self._grant_role_session(
                f"arn:aws:execute-api:{REGION}:{ACCOUNT}:rest1/*/*/*")
            rows = audit.collect_iam(session) + [make_row("APIGatewayRestApi", "rest1")]
            audit.resolve_edges(rows)
            self.assertIn("APIGatewayRestApi:rest1",
                          audit.render_connections(self._row(rows, "orders-role")))
            self.assertIn("used by",
                          audit.render_connections(self._row(rows, "rest1")))

    def test_execute_api_grant_resolves_an_http_api_too(self):
        """REST and HTTP APIs share one ARN shape, so no target_service; a v2 API still resolves."""
        with self._isolated("iamrole.apigateway.execute-api-grant", audit.CONN_ON):
            session = self._grant_role_session(
                f"arn:aws:execute-api:{REGION}:{ACCOUNT}:http1/$default/*")
            rows = audit.collect_iam(session) + [make_row("APIGatewayV2Api", "http1")]
            audit.resolve_edges(rows)
            self.assertIn("APIGatewayV2Api:http1",
                          audit.render_connections(self._row(rows, "orders-role")))

    def test_a_blanket_execute_api_grant_names_no_api(self):
        """"every API in the account" is not a dependency on one. Left to
        fall through, it stays visible as an unmatched grant instead."""
        with self._isolated("iamrole.apigateway.execute-api-grant", audit.CONN_ON):
            session = self._grant_role_session(
                f"arn:aws:execute-api:{REGION}:{ACCOUNT}:*/*/*/*")
            rows = audit.collect_iam(session) + [make_row("APIGatewayRestApi", "rest1")]
            audit.resolve_edges(rows)
            self.assertNotIn("APIGatewayRestApi:rest1",
                             audit.render_connections(self._row(rows, "orders-role")))

    def test_prefix_grant_reaches_every_table_the_pattern_covers(self):
        """A prefix grant links every matching table, not only the exactly named one."""
        with self._isolated("iamrole.any.resource-grant-prefix", audit.CONN_ON):
            rows = audit.collect_iam(self._grant_role_session(
                f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/orders*"))
            rows += [make_row("DynamoDBTable", n)
                     for n in ("orders", "orders-dev", "orders-test", "invoices")]
            links = audit.resolve_edges(rows)
            role = self._row(rows, "orders-role")

            for name in ("orders", "orders-dev", "orders-test"):
                self.assertIn(f"DynamoDBTable:{name}", audit.render_connections(role),
                              f"{name} is covered by the pattern but was not linked")
                self.assertIn("used by", audit.render_connections(self._row(rows, name)))
            self.assertNotIn("invoices", audit.render_connections(role))
            self.assertEqual(len(links), 3)

            quiet(audit.apply_project_grouping, rows, edge_links=links)
            self.assertEqual(
                {self._row(rows, n)["project_group"]
                 for n in ("orders", "orders-dev", "orders-test", "orders-role")},
                {role["project_group"]})

    def test_prefix_grant_names_the_row_it_matched_not_the_pattern(self):
        """Every table under "orders*" reporting itself as "orders" would make
        the connections column disagree with the graph about what exists."""
        with self._isolated("iamrole.any.resource-grant-prefix", audit.CONN_ON):
            rows = audit.collect_iam(self._grant_role_session(
                f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/orders*"))
            rows += [make_row("DynamoDBTable", "orders-dev")]
            audit.resolve_edges(rows)
            role = self._row(rows, "orders-role")
            self.assertIn("DynamoDBTable:orders-dev", audit.render_connections(role))
            self.assertEqual(
                [r["target_id"] for r in role["_references"]
                 if r["kind"] == "resolved"], ["orders-dev"])

    def test_prefix_grant_over_the_match_cap_is_refused_and_says_so(self):
        """A pattern this broad is a naming convention: refused, but recorded."""
        with self._isolated("iamrole.any.resource-grant-prefix", audit.CONN_ON):
            rows = audit.collect_iam(self._grant_role_session(
                f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/orders*"))
            rows += [make_row("DynamoDBTable", f"orders-{i}")
                     for i in range(audit.ARN_PREFIX_MAX_MATCHES + 1)]
            links = audit.resolve_edges(rows)
            role = self._row(rows, "orders-role")

            self.assertEqual(links, [])
            self.assertIn("blanket grant", audit.render_connections(role))
            self.assertNotIn("DynamoDBTable:orders-0", audit.render_connections(role))

    def test_prefix_grant_never_crosses_resource_type(self):
        """Prefix matching widens a cross-type collision from one row to all of
        them, so the type guard is mandatory here, not advisable."""
        with self._isolated("iamrole.any.resource-grant-prefix", audit.CONN_ON):
            rows = audit.collect_iam(self._grant_role_session(
                f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/orders*"))
            rows += [make_row("S3Bucket", "orders-data", region="global")]
            links = audit.resolve_edges(rows)
            self.assertEqual(links, [])
            self.assertNotIn("orders-data", audit.render_connections(self._row(rows, "orders-role")))

    def test_a_trailing_wildcard_resolves_to_a_typed_prefix(self):
        self.assertEqual(
            audit.resource_id_prefix_from_arn(
                f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/orders*"),
            ("orders", "DynamoDBTable"))

    def test_short_and_interior_wildcards_are_refused(self):
        """A short prefix claims half the account; an interior one would need
        real glob matching, which buys new ways to link unrelated resources."""
        for arn in (f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/or*",
                    f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/*-prod",
                    f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/or*ers*",
                    f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/orders"):
            with self.subTest(arn=arn):
                self.assertEqual(audit.resource_id_prefix_from_arn(arn), (None, None))

    def test_disabled_prefix_grants_are_still_reported_as_unmatched(self):
        """Switching the type off must not make the pattern vanish from the
        report - the row should say it grants something it couldn't match."""
        with self._isolated("iamrole.any.resource-grant-prefix", audit.CONN_OFF):
            rows = audit.collect_iam(self._grant_role_session(
                f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/orders*"))
            self.assertIn("table/orders*", audit.render_connections(self._row(rows, "orders-role")))

    def test_wildcard_only_resource_grants_are_ignored(self):
        """"Resource": "*" says nothing about which resources are touched, so
        it must not become an edge to everything."""
        arns = audit.extract_resource_arns({"Statement": [
            {"Effect": "Allow", "Action": "s3:*", "Resource": "*"},
            {"Effect": "Allow", "Action": "s3:*", "Resource": "arn:aws:s3:::*"},
        ]})
        self.assertEqual(arns, set())

    def test_wildcard_inside_a_resource_name_is_not_matched_to_one_row(self):
        """"table/orders*" is a prefix, not a specific resource."""
        rid, service = audit.probable_resource_id_from_arn(
            f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/orders*")
        self.assertIsNone(rid)
        self.assertIsNone(service)

    def test_wildcards_elsewhere_in_the_arn_still_resolve(self):
        """Region/account wildcards are normal in real policies and must not
        disqualify an otherwise exact resource name."""
        rid, service = audit.probable_resource_id_from_arn(
            "arn:aws:dynamodb:*:*:table/orders")
        self.assertEqual((rid, service), ("orders", "DynamoDBTable"))

    def test_aws_managed_policies_are_not_read_for_grants(self):
        """They are broad and wildcarded, so reading them costs API calls and
        adds no resource-specific signal."""
        client = FakeSession({"iam": {
            "list_role_policies": {"PolicyNames": []},
            "list_attached_role_policies": {"AttachedPolicies": [
                {"PolicyArn": "arn:aws:iam::aws:policy/ReadOnlyAccess"}]},
        }}).client("iam")
        grants = audit.gather_role_policy_grants(client, "orders-role")
        self.assertEqual(grants, set())
        self.assertNotIn("get_policy", client.operations_called())


class RunsAsConnectionTests(ConnectionTypeTestCase):
    """Execution-role links: a role shared by several projects no longer merges them."""

    def test_lambda_iamrole_execution_role(self):
        def build():
            session = lambda_session([function_payload(Role=(
                f"arn:aws:iam::{ACCOUNT}:role/orders-exec"))])
            rows = audit.collect_lambda_functions(session, REGION)
            return rows + [make_row("IAMRole", "orders-exec", region="global")]
        self.assert_three_states("lambda.iamrole.execution-role", build,
                                 "orders-fn", "orders-exec")

    def test_ec2_iamrole_instance_profile(self):
        def build():
            session = ec2_session([instance_payload(
                IamInstanceProfile={"Arn": f"arn:aws:iam::{ACCOUNT}:instance-profile/web-role"})])
            rows = audit.collect_ec2_instances(session, REGION)
            return rows + [make_row("IAMRole", "web-role", region="global")]
        self.assert_three_states("ec2.iamrole.instance-profile", build,
                                 "i-0abc", "web-role")

    def test_a_role_shared_by_two_projects_does_not_merge_them(self):
        a = make_row("LambdaFunction", "orders-fn", tags={"Project": "orders"})
        b = make_row("LambdaFunction", "billing-fn", tags={"Project": "billing"})
        role = make_row("IAMRole", "lambda-basic-exec", region="global")
        for row in (a, b):
            audit.add_edge(row, "lambda-basic-exec", "runs as", "config Role",
                           conn_type="lambda.iamrole.execution-role",
                           target_service="IAMRole")
        rows = [a, b, role]
        links = audit.resolve_edges(rows)
        quiet(audit.apply_project_grouping, rows, edge_links=links)

        self.assertNotEqual(a["project_id"], b["project_id"])
        self.assertEqual(role["membership"], "shared")
        self.assertTrue([r for r in a["_references"] if r["kind"] == "resolved"],
                        "the dependency itself is still recorded")


class CognitoConnectionTests(ConnectionTypeTestCase):

    def test_cognito_lambda_trigger(self):
        def build():
            rows = audit.collect_cognito_user_pools(cognito_session({"LambdaConfig": {
                "CreateAuthChallenge": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:orders-fn",
            }}), REGION)
            return rows + [make_row("LambdaFunction", "orders-fn")]
        self.assert_three_states("cognito.lambda.trigger", build,
                                 "us-east-1_abc123", "orders-fn")

    def test_custom_sender_trigger_arn_is_nested_under_a_dict(self):
        """Most triggers are a bare ARN string; the custom sender ones nest it
        alongside a key version, so a naive read gets a dict."""
        rows = audit.collect_cognito_user_pools(cognito_session({"LambdaConfig": {
            "CustomEmailSender": {"LambdaArn": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:orders-fn",
                                  "LambdaVersion": "V1_0"},
        }}), REGION)
        self.assertEqual(find_edge(rows[0], "cognito.lambda.trigger")["target_id"], "orders-fn")

    def test_cognito_iamrole_sms_caller(self):
        def build():
            rows = audit.collect_cognito_user_pools(cognito_session({
                "SmsConfiguration": {"SnsCallerArn": f"arn:aws:iam::{ACCOUNT}:role/sms-role"},
            }), REGION)
            return rows + [make_row("IAMRole", "sms-role", region="global")]
        self.assert_three_states("cognito.iamrole.sms-caller", build,
                                 "us-east-1_abc123", "sms-role")

    def test_service_linked_role_arn_path_resolves_to_the_bare_name(self):
        self.assertEqual(
            audit.probable_resource_id_from_arn(
                f"arn:aws:iam::{ACCOUNT}:role/aws-service-role/rds.amazonaws.com/AWSServiceRoleForRDS"),
            ("AWSServiceRoleForRDS", "IAMRole"))


class CertificateConnectionTests(ConnectionTypeTestCase):

    def test_acmcertificate_any_in_use(self):
        def build():
            lb = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/app/web-alb/0123"
            session = FakeSession({"acm": {
                "list_certificates": {"CertificateSummaryList": [{"CertificateArn": "arn:cert/abc-123"}]},
                "describe_certificate": {"Certificate": {"Status": "ISSUED", "InUseBy": [lb]}},
                "list_tags_for_certificate": {"Tags": []},
            }})
            return audit.collect_acm_certificates(session, REGION) + [make_row("LoadBalancer", "web-alb")]
        self.assert_three_states("acmcertificate.any.in-use", build, "abc-123", "web-alb")

    def test_a_classic_load_balancer_arn_names_its_row(self):
        arn = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/legacy"
        self.assertEqual(audit.probable_resource_id_from_arn(arn), ("legacy", "LoadBalancer"))


class ParameterConnectionTests(ConnectionTypeTestCase):

    def test_ssmparameter_kmskey_encryption(self):
        def build():
            session = FakeSession({"ssm": {
                "describe_parameters": {"Parameters": [{"Name": "/app/config", "KeyId": "key-0123"}]},
                "list_tags_for_resource": {"TagList": []},
            }})
            return audit.collect_ssm_parameters(session, REGION) + [make_row("KMSKey", "key-0123")]
        self.assert_three_states("ssmparameter.kmskey.encryption", build, "/app/config", "key-0123")

    def test_an_aws_managed_key_alias_links_nothing(self):
        session = FakeSession({"ssm": {
            "describe_parameters": {"Parameters": [{"Name": "/app/config", "KeyId": "alias/aws/ssm"}]},
            "list_tags_for_resource": {"TagList": []},
        }})
        self.assertEqual(audit.collect_ssm_parameters(session, REGION)[0].get("_edges", []), [])


class SecretConnectionTests(ConnectionTypeTestCase):

    @staticmethod
    def _secret(**fields):
        secret = {"Name": "api-token", "CreatedDate": recent()}
        secret.update(fields)
        return audit.collect_secrets(
            FakeSession({"secretsmanager": {"list_secrets": {"SecretList": [secret]}}}), REGION)

    def test_secret_kmskey_encryption(self):
        key_arn = f"arn:aws:kms:{REGION}:{ACCOUNT}:key/1234abcd"
        self.assert_three_states("secret.kmskey.encryption",
                                 lambda: self._secret(KmsKeyId=key_arn) + [make_row("KMSKey", "1234abcd")],
                                 "api-token", "1234abcd")

    def test_secret_lambda_rotation(self):
        fn_arn = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:rotator"
        self.assert_three_states("secret.lambda.rotation",
                                 lambda: self._secret(RotationLambdaARN=fn_arn)
                                 + [make_row("LambdaFunction", "rotator")],
                                 "api-token", "rotator")

    def test_an_aws_managed_key_alias_names_no_collected_key(self):
        rows = self._secret(KmsKeyId="alias/aws/secretsmanager")
        self.assertEqual(rows[0]["_edges"], [])


if __name__ == "__main__":
    unittest.main()
