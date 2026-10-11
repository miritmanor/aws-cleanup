"""Connection types for deployment: CodeBuild/CodePipeline, CloudFormation,
Amplify, Elastic Beanstalk."""

import unittest

from .fakes import FakeSession, client_error, find_edge, make_row, audit, recent
from .connections_base import ACCOUNT, ConnectionTypeTestCase, REGION, quiet


class CicdConnectionTests(ConnectionTypeTestCase):

    def test_codebuild_iamrole_service_role(self):
        def build():
            session = FakeSession({"codebuild": {
                "list_projects": {"projects": ["api-build"]},
                "batch_get_projects": {"projects": [{
                    "name": "api-build", "created": recent(),
                    "serviceRole": f"arn:aws:iam::{ACCOUNT}:role/build-role"}]},
                "list_builds_for_project": {"ids": []},
            }})
            return (audit.collect_codebuild_projects(session, REGION)
                    + [make_row("IAMRole", "build-role", region="global")])
        self.assert_three_states("codebuild.iamrole.service-role", build, "api-build", "build-role")

    @staticmethod
    def _pipeline(**pipeline):
        session = FakeSession({"codepipeline": {
            "list_pipelines": {"pipelines": [{"name": "release", "created": recent()}]},
            "list_pipeline_executions": {"pipelineExecutionSummaries": []},
            "get_pipeline": {"pipeline": dict({"stages": []}, **pipeline)},
        }})
        return audit.collect_codepipelines(session, REGION)

    def test_codepipeline_iamrole_role(self):
        self.assert_three_states(
            "codepipeline.iamrole.role",
            lambda: self._pipeline(roleArn=f"arn:aws:iam::{ACCOUNT}:role/pipe-role")
            + [make_row("IAMRole", "pipe-role", region="global")], "release", "pipe-role")

    def test_codepipeline_s3bucket_artifact_store(self):
        self.assert_three_states(
            "codepipeline.s3bucket.artifact-store",
            lambda: self._pipeline(artifactStore={"type": "S3", "location": "pipe-artifacts"})
            + [make_row("S3Bucket", "pipe-artifacts", region="eu-west-1")], "release", "pipe-artifacts")

    def test_codepipeline_codebuild_action(self):
        stages = [{"name": "Build", "actions": [{
            "actionTypeId": {"provider": "CodeBuild"}, "configuration": {"ProjectName": "api-build"}}]}]
        self.assert_three_states(
            "codepipeline.codebuild.action",
            lambda: self._pipeline(stages=stages) + [make_row("CodeBuildProject", "api-build")],
            "release", "api-build")

    def test_an_artifact_bucket_is_deployment_tier(self):
        rows = self._pipeline(artifactStore={"location": "pipe-artifacts"})
        rows.append(make_row("S3Bucket", "pipe-artifacts", region="eu-west-1"))
        audit.resolve_edges(rows)
        audit.apply_tiers(rows)
        self.assertEqual(rows[1]["tier"], audit.TIER_DEPLOYMENT)


class CloudFormationConnectionTests(ConnectionTypeTestCase):

    def test_cfnstack_parent(self):
        parent_id = f"arn:aws:cloudformation:{REGION}:{ACCOUNT}:stack/root-stack/abc"
        child_id = f"arn:aws:cloudformation:{REGION}:{ACCOUNT}:stack/child-stack/def"

        def build():
            session = FakeSession({"cloudformation": {"list_stacks": {"StackSummaries": [
                {"StackName": "root-stack", "StackId": parent_id,
                 "StackStatus": "CREATE_COMPLETE", "CreationTime": recent()},
                {"StackName": "child-stack", "StackId": child_id, "ParentId": parent_id,
                 "StackStatus": "CREATE_COMPLETE", "CreationTime": recent()},
            ]}}})
            return audit.collect_cloudformation_stacks(session, REGION)
        self.assert_three_states("cfnstack.cfnstack.parent", build, child_id, parent_id)


class StackMembershipTests(unittest.TestCase):
    """A deployment owns what it deployed: every live stack is walked."""

    def _session(self, pages):
        return FakeSession({"cloudformation": {"list_stack_resources": pages}})

    def test_a_stack_provisions_its_resources(self):
        stack = make_row("CloudFormationStack", "arn:stack/orders", "orders-stack")
        fn = make_row("LambdaFunction", "orders-fn")
        rows = [stack, fn]
        audit.apply_stack_membership(rows, self._session({
            "StackResourceSummaries": [{
                "ResourceType": "AWS::Lambda::Function",
                "PhysicalResourceId": "orders-fn",
                "LogicalResourceId": "OrdersFunction",
            }]}))
        audit.resolve_edges(rows)
        quiet(audit.apply_project_grouping, rows,
              edge_links=audit.resolve_edges(rows))

        self.assertIn("provisions", audit.render_connections(stack))
        self.assertEqual(fn["project_id"], stack["project_id"])
        self.assertTrue(fn["project_id"], "cfnstack.any.stack-resource groups")

    def test_nested_stacks_are_walked_and_recorded(self):
        calls = []

        def pages(**kwargs):
            calls.append(kwargs.get("StackName"))
            if kwargs.get("StackName") == "arn:stack/root":
                return {"StackResourceSummaries": [{
                    "ResourceType": "AWS::CloudFormation::Stack",
                    "PhysicalResourceId": "arn:stack/child",
                    "LogicalResourceId": "api",
                }]}
            return {"StackResourceSummaries": [{
                "ResourceType": "AWS::DynamoDB::Table",
                "PhysicalResourceId": "orders",
                "LogicalResourceId": "OrdersTable",
            }]}

        root = make_row("CloudFormationStack", "arn:stack/root", "root")
        table = make_row("DynamoDBTable", "orders")
        rows = [root, table]
        audit.apply_stack_membership(rows, self._session(pages))
        audit.resolve_edges(rows)

        self.assertIn("arn:stack/child", calls, "the nested stack is walked")
        self.assertIn("orders", audit.render_connections(root),
                      "a resource two levels down still belongs to the deployment")

    def test_a_stack_is_walked_only_once(self):
        """A nested stack is also a top-level row. Walking it twice pays for
        the same pages twice and says nothing new."""
        calls = []

        def pages(**kwargs):
            calls.append(kwargs.get("StackName"))
            return {"StackResourceSummaries": []}

        rows = [make_row("CloudFormationStack", "arn:stack/a", "a"),
                make_row("CloudFormationStack", "arn:stack/a", "a")]
        audit.apply_stack_membership(rows, self._session(pages))
        self.assertEqual(calls.count("arn:stack/a"), 1)

    def test_an_unscanned_resource_type_is_not_invented(self):
        """A stack provisions plenty this script does not inventory. Those
        stay unresolved references rather than becoming rows."""
        stack = make_row("CloudFormationStack", "arn:stack/orders", "orders-stack")
        rows = [stack]
        audit.apply_stack_membership(rows, self._session({
            "StackResourceSummaries": [{
                "ResourceType": "AWS::CloudFront::Distribution",
                "PhysicalResourceId": "E123ABC",
                "LogicalResourceId": "CDN",
            }]}))
        audit.resolve_edges(rows)

        self.assertEqual([r for r in rows if r["service"] == "CloudFrontDistribution"], [])
        self.assertEqual(len(rows), 1, "no row was invented for it")

    def test_a_failed_walk_keeps_what_it_read_and_records_coverage(self):
        from aws_resource_audit import coverage as cov

        stack = make_row("CloudFormationStack", "arn:stack/orders", "orders")
        with cov.ledger_of() as ledger:
            audit.apply_stack_membership([stack], self._session(
                client_error(operation="ListStackResources")))
        self.assertEqual(ledger.status("CloudFormationStack",
                                       capability=cov.DEPENDENCIES), cov.DENIED)


class AmplifyConnectionTests(ConnectionTypeTestCase):

    APP_ID = "d1234abcd"

    def _amplify_session(self, backend_envs, stack_resources=None,
                         app_env=None, branches=None):
        return FakeSession({
            "amplify": {
                "list_backend_environments": {"backendEnvironments": backend_envs},
                "get_app": {"app": {"environmentVariables": app_env or {}}},
                "list_branches": {"branches": branches or []},
            },
            "cloudformation": {"list_stack_resources": {
                "StackResourceSummaries": stack_resources or []}},
        })

    API_URL = "https://rest1.execute-api.us-east-1.amazonaws.com/prod/"

    def test_amplify_apigateway_env_var_execute_api_url(self):
        def build():
            rows = [
                make_row("AmplifyApp", self.APP_ID, name="unrelated-name"),
                make_row("APIGatewayRestApi", "rest1", name="orders-api"),
            ]
            audit.apply_amplify_api_links(rows, self._amplify_session(
                [], app_env={"REACT_APP_API_GATEWAY_URL": self.API_URL}))
            return rows
        self.assert_three_states(
            "amplify.apigateway.env-var-execute-api-url", build, self.APP_ID, "rest1")

    def test_a_branch_environment_variable_counts_too(self):
        """Branch-level variables are read: an app may name no API at its own level."""
        with self._isolated("amplify.apigateway.env-var-execute-api-url", audit.CONN_ON):
            rows = [
                make_row("AmplifyApp", self.APP_ID, name="unrelated-name"),
                make_row("APIGatewayRestApi", "rest1", name="orders-api"),
            ]
            audit.apply_amplify_api_links(rows, self._amplify_session([], branches=[
                {"branchName": "main",
                 "environmentVariables": {"REACT_APP_API": self.API_URL}},
            ]))
            audit.resolve_edges(rows)
            self.assertIn("APIGatewayRestApi:rest1", audit.render_connections(rows[0]))
            self.assertIn("branch main", rows[0]["connections"],
                          "the connections text should say where it was configured")

    def test_an_api_named_by_env_var_is_not_also_guessed_at_by_name(self):
        """When both mechanisms reach one API, the configured reference wins over the name match."""
        rows = [
            make_row("AmplifyApp", self.APP_ID, name="ordersweb"),
            make_row("APIGatewayRestApi", "rest1", name="ordersweb-api"),
        ]
        audit.apply_amplify_api_links(rows, self._amplify_session(
            [], app_env={"REACT_APP_API_GATEWAY_URL": self.API_URL}))
        conn_types = {e["conn_type"] for e in rows[0]["_edges"]}
        self.assertIn("amplify.apigateway.env-var-execute-api-url", conn_types)
        self.assertNotIn("amplify.apigateway.name-match", conn_types)

    def test_amplify_cfn_stack_walk(self):
        def build():
            rows = [
                make_row("AmplifyApp", self.APP_ID, name="orders-web"),
                make_row("DynamoDBTable", "orders"),
            ]
            session = self._amplify_session(
                [{"stackName": "amplify-orders-dev"}],
                [{"ResourceType": "AWS::DynamoDB::Table", "PhysicalResourceId": "orders"}],
            )
            audit.apply_amplify_api_links(rows, session)
            return rows
        self.assert_three_states(
            "amplify.any.cfn-stack-walk", build, self.APP_ID, "orders")

    def test_amplify_cfnstack_root_stack(self):
        def build():
            rows = [
                make_row("AmplifyApp", self.APP_ID, name="orders-web"),
                make_row("CloudFormationStack", "stack-arn-1", name="amplify-orders-dev"),
            ]
            audit.apply_amplify_api_links(
                rows, self._amplify_session([{"stackName": "amplify-orders-dev"}]))
            return rows
        # Matched by stack NAME, since ListBackendEnvironments returns no ARN -
        # so the target row is found via its name, not its resource_id.
        with self._isolated("amplify.cfnstack.root-stack", audit.CONN_ON):
            rows = build()
            links = audit.resolve_edges(rows)
            edge = find_edge(rows[0], "amplify.cfnstack.root-stack")
            self.assertIsNotNone(edge)
            self.assertEqual(edge["target_match"], "name")
            self.assertTrue(links)
        with self._isolated("amplify.cfnstack.root-stack", audit.CONN_OFF):
            rows = build()
            self.assertIsNone(find_edge(rows[0], "amplify.cfnstack.root-stack"))

    def test_amplify_apigateway_name_match(self):
        def build():
            rows = [
                make_row("AmplifyApp", self.APP_ID, name="ordersweb"),
                make_row("APIGatewayRestApi", "rest1", name="ordersweb-api"),
            ]
            # No backend environments at all -> falls back to the name heuristic.
            audit.apply_amplify_api_links(rows, self._amplify_session([]))
            return rows
        self.assert_three_states(
            "amplify.apigateway.name-match", build, self.APP_ID, "rest1")

    def test_the_stack_walk_wins_outright_for_an_api_it_claimed(self):
        """A guess on top of a known-good answer is noise. Where the walk found
        the API itself, the name match must not restate it."""
        rows = [
            make_row("AmplifyApp", self.APP_ID, name="ordersweb"),
            make_row("APIGatewayRestApi", "rest1", name="ordersweb-api"),
        ]
        audit.apply_amplify_api_links(rows, self._amplify_session(
            [{"stackName": "amplify-orders-dev"}],
            [{"ResourceType": "AWS::ApiGateway::RestApi", "PhysicalResourceId": "rest1"}],
        ))
        conn_types = {e["conn_type"] for e in rows[0]["_edges"]}
        self.assertIn("amplify.any.cfn-stack-walk", conn_types)
        self.assertNotIn("amplify.apigateway.name-match", conn_types)

    def test_an_api_the_backend_did_not_provision_is_still_name_matched(self):
        """The name match still runs after a successful stack walk: the walk sees what
        Amplify provisioned, not a pre-existing API the front end calls."""
        rows = [
            make_row("AmplifyApp", self.APP_ID, name="ordersweb"),
            make_row("DynamoDBTable", "orders"),
            make_row("APIGatewayRestApi", "rest1", name="ordersweb-api"),
        ]
        # The walk succeeds and claims the table - but never mentions the API.
        audit.apply_amplify_api_links(rows, self._amplify_session(
            [{"stackName": "amplify-orders-dev"}],
            [{"ResourceType": "AWS::DynamoDB::Table", "PhysicalResourceId": "orders"}],
        ))
        conn_types = {e["conn_type"] for e in rows[0]["_edges"]}
        self.assertIn("amplify.any.cfn-stack-walk", conn_types)
        self.assertIn("amplify.apigateway.name-match", conn_types,
                      "an unprovisioned API must still be guessable by name")
        self.assertIn("LOW CONFIDENCE", rows[0]["connections"],
                      "the guess must still be worded as a guess")

    def test_name_match_is_report_only_by_default(self):
        self.assertEqual(
            audit.CONNECTION_TYPES_BY_ID["amplify.apigateway.name-match"].default_state,
            audit.CONN_REPORT_ONLY)


class ElasticBeanstalkConnectionTests(ConnectionTypeTestCase):

    def _build(self, **overrides):
        env = {
            "EnvironmentId": "e-abc123", "EnvironmentName": "orders-env",
            "ApplicationName": "orders-app", "VersionLabel": "v1",
            "EnvironmentArn": f"arn:aws:elasticbeanstalk:{REGION}:{ACCOUNT}:environment/orders-app/orders-env",
            "Status": "Ready", "Health": "Green",
            "DateCreated": recent(30), "DateUpdated": recent(1),
        }
        responses = {
            "describe_environments": {"Environments": [env]},
            "list_tags_for_resource": {"ResourceTags": []},
            "describe_environment_resources": {"EnvironmentResources": {
                "Instances": [{"Id": "i-0abc123"}],
                "LoadBalancers": [{"Name": "orders-alb"}],
            }},
            "describe_application_versions": {"ApplicationVersions": [{
                "SourceBundle": {"S3Bucket": "orders-deploy-bucket", "S3Key": "orders-app/v1.zip"},
            }]},
        }
        responses.update(overrides)
        session = FakeSession({"elasticbeanstalk": responses})
        rows = audit.collect_elastic_beanstalk_environments(session, REGION)
        # Each target is from another collector, so it is stood in directly.
        return rows + [
            make_row("EC2Instance", "i-0abc123"),
            make_row("LoadBalancer", "orders-alb"),
            make_row("S3Bucket", "orders-deploy-bucket"),
            make_row("ElasticBeanstalkApplication", "orders-app"),
        ]

    def test_ec2_instance(self):
        self.assert_three_states("elasticbeanstalk.ec2instance.environment-resource",
                                 self._build, "e-abc123", "i-0abc123")

    def test_load_balancer(self):
        self.assert_three_states("elasticbeanstalk.loadbalancer.environment-resource",
                                 self._build, "e-abc123", "orders-alb")

    def test_deployment_bucket(self):
        self.assert_three_states("elasticbeanstalk.s3bucket.deployment-artifact",
                                 self._build, "e-abc123", "orders-deploy-bucket")

    def test_environment_names_its_application(self):
        self.assert_three_states("elasticbeanstalk.ebapplication.application-name",
                                 self._build, "e-abc123", "orders-app")


class ElasticBeanstalkApplicationConnectionTests(ConnectionTypeTestCase):
    """Beanstalk applications: a bucket named by a source bundle, or AWS's service bucket
    confirmed by probing for the app's folder."""

    SERVICE_BUCKET = f"elasticbeanstalk-{REGION}-{ACCOUNT}"

    def _build(self, key_count=1, versions=None):
        app_arn = f"arn:aws:elasticbeanstalk:{REGION}:{ACCOUNT}:application/orders-app"
        if versions is None:
            versions = [{"SourceBundle": {"S3Bucket": "orders-bundle-bucket",
                                          "S3Key": "orders-app/v1.zip"}}]
        session = FakeSession({
            "elasticbeanstalk": {
                "describe_applications": {"Applications": [{
                    "ApplicationName": "orders-app", "ApplicationArn": app_arn,
                    "DateCreated": recent(60), "DateUpdated": recent(3),
                    "Versions": ["v1"],
                }]},
                "list_tags_for_resource": {"ResourceTags": []},
                "describe_application_versions": {"ApplicationVersions": versions},
            },
            "s3": {"list_objects_v2": {"KeyCount": key_count}},
        })
        rows = audit.collect_elastic_beanstalk_applications(session, REGION)
        return rows + [
            make_row("S3Bucket", "orders-bundle-bucket"),
            make_row("S3Bucket", self.SERVICE_BUCKET),
        ]

    def test_source_bundle_bucket(self):
        self.assert_three_states("ebapplication.s3bucket.source-bundle",
                                 self._build, "orders-app", "orders-bundle-bucket")

    def test_service_bucket_confirmed_by_the_folder_probe(self):
        self.assert_three_states(
            "ebapplication.s3bucket.service-bucket",
            lambda: self._build(versions=[]), "orders-app", self.SERVICE_BUCKET)

    def test_the_probe_targets_a_constructed_prefix_and_never_reads_keys(self):
        """The probe uses a prefix built from AWS's name with MaxKeys=1: yes or no, no
        object names read."""
        app_arn = f"arn:aws:elasticbeanstalk:{REGION}:{ACCOUNT}:application/orders-app"
        session = FakeSession({
            "elasticbeanstalk": {
                "describe_applications": {"Applications": [{
                    "ApplicationName": "orders-app", "ApplicationArn": app_arn,
                    "DateCreated": recent(60), "DateUpdated": recent(3),
                }]},
                "list_tags_for_resource": {"ResourceTags": []},
                "describe_application_versions": {"ApplicationVersions": []},
            },
            "s3": {"list_objects_v2": {"KeyCount": 1}},
        })
        audit.collect_elastic_beanstalk_applications(session, REGION)
        kwargs = next(k for op, k in session.get("s3").calls if op == "list_objects_v2")
        self.assertEqual(kwargs["Bucket"], self.SERVICE_BUCKET)
        self.assertEqual(kwargs["Prefix"],
                         "resources/_runtime/_embedded_extensions/orders-app/")
        self.assertEqual(kwargs["MaxKeys"], 1)


if __name__ == "__main__":
    unittest.main()
