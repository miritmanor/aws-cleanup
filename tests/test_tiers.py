"""The runtime/deployment split: each rule on its own evidence, their precedence, and
no evidence defaulting to runtime (never a guessed "deployment")."""

import os
import re
import unittest

from .fakes import FakeSession, make_row

import aws_resource_audit as audit


class TierRuleTests(unittest.TestCase):
    """One test per rule, each on the evidence that rule reads and no other."""

    def test_amplify_deployment_artifacts_bucket(self):
        app = make_row("AmplifyApp", "d111", "bakery")
        app["_amplify_deployment_artifacts"] = ["bakery-deploy-abc123"]
        bucket = make_row("S3Bucket", "bakery-deploy-abc123", region="global")
        audit.apply_tiers([app, bucket])
        self.assertEqual(bucket["tier"], audit.TIER_DEPLOYMENT)
        # The reason names the app, because on an account with three Amplify
        # apps "some app called it an artifact store" is not actionable.
        self.assertIn("bakery", bucket["why_tier"])

    def test_deployment_cloudformation_logical_id(self):
        app = make_row("AmplifyApp", "d111", "bakery")
        app["_amplify_provenance"] = {
            "bakery-userpool-client": {"logical_id": "UserPoolClientLambda",
                                       "category": "auth",
                                       "type": "AWS::Lambda::Function"},
        }
        fn = make_row("LambdaFunction", "bakery-userpool-client")
        audit.apply_tiers([app, fn])
        self.assertEqual(fn["tier"], audit.TIER_DEPLOYMENT)
        self.assertIn("UserPoolClientLambda", fn["why_tier"])

    def test_logical_id_match_is_case_insensitive(self):
        """CloudFormation ids are free-form casing and the set is lowercased."""
        app = make_row("AmplifyApp", "d111", "bakery")
        app["_amplify_provenance"] = {
            "b": {"logical_id": "deploymentbucket", "category": "",
                  "type": "AWS::S3::Bucket"},
        }
        bucket = make_row("S3Bucket", "b", region="global")
        audit.apply_tiers([app, bucket])
        self.assertEqual(bucket["tier"], audit.TIER_DEPLOYMENT)

    def test_amplify_category_stack_is_runtime(self):
        """The category stacks ARE the application - this is the rule that
        keeps the deployment verdict from swallowing everything Amplify made."""
        app = make_row("AmplifyApp", "d111", "bakery")
        app["_amplify_provenance"] = {
            "bakery-stores": {"logical_id": "StoresTable", "category": "storage",
                              "type": "AWS::DynamoDB::Table"},
        }
        table = make_row("DynamoDBTable", "bakery-stores")
        audit.apply_tiers([app, table])
        self.assertEqual(table["tier"], audit.TIER_RUNTIME)
        self.assertIn("storage", table["why_tier"])

    def test_root_stack_resource_is_deployment(self):
        """Category "" means it sat directly in the backend root stack, which
        holds what deploys the categories rather than anything the app calls."""
        app = make_row("AmplifyApp", "d111", "bakery")
        app["_amplify_provenance"] = {
            "bakery-deploy": {"logical_id": "SomethingScaffoldy", "category": "",
                              "type": "AWS::S3::Bucket"},
        }
        bucket = make_row("S3Bucket", "bakery-deploy", region="global")
        audit.apply_tiers([app, bucket])
        self.assertEqual(bucket["tier"], audit.TIER_DEPLOYMENT)

    def test_elastic_beanstalk_applications_are_deployment(self):
        """A Beanstalk application is deployment; its environment keeps the default."""
        app = make_row("ElasticBeanstalkApplication", "bakery", "bakery")
        env = make_row("ElasticBeanstalkEnvironment", "e-abc123", "bakery-env")
        audit.apply_tiers([app, env])
        self.assertEqual(app["tier"], audit.TIER_DEPLOYMENT)
        self.assertEqual(env["tier"], audit.TIER_RUNTIME)
        self.assertEqual(env["why_tier"], audit.DEFAULT_WHY_TIER)

    def test_the_eb_service_bucket_is_deployment(self):
        """A bucket an EB application deploys from is deployment."""
        app = make_row("ElasticBeanstalkApplication", "bakery", "bakery")
        bucket = make_row("S3Bucket", "elasticbeanstalk-us-east-1-111122223333",
                          region="global")
        audit.add_edge(app, "elasticbeanstalk-us-east-1-111122223333",
                       "stores deployment artifacts in",
                       "folder exists in the service bucket",
                       conn_type="ebapplication.s3bucket.service-bucket",
                       target_service="S3Bucket")
        rows = [app, bucket]
        audit.resolve_edges(rows)
        audit.apply_tiers(rows)
        self.assertEqual(bucket["tier"], audit.TIER_DEPLOYMENT)
        # Names the application, for the same reason the Amplify rule does.
        self.assertIn("bakery", bucket["why_tier"])
        self.assertIn("Elastic Beanstalk", bucket["why_tier"])

    def test_a_source_bundle_bucket_outside_the_scan_classifies_nothing(self):
        """An unresolved bundle bucket (not a row) classifies nothing."""
        app = make_row("ElasticBeanstalkApplication", "bakery", "bakery")
        audit.add_edge(app, "elasticbeanstalk-us-east-1", "deploys from",
                       "describe_application_versions SourceBundle.S3Bucket",
                       conn_type="ebapplication.s3bucket.source-bundle",
                       target_service="S3Bucket", assert_exists=False)
        unrelated = make_row("S3Bucket", "elasticbeanstalk-us-east-1",
                             region="global")
        rows = [app]  # the bucket is deliberately NOT in the scan
        audit.resolve_edges(rows)
        audit.apply_tiers(rows + [unrelated])
        self.assertEqual(unrelated["tier"], audit.TIER_RUNTIME)
        self.assertEqual(unrelated["why_tier"], audit.DEFAULT_WHY_TIER)

    def test_cloudformation_stacks_are_deployment(self):
        stack = make_row("CloudFormationStack", "arn:...:stack/infra/1", "infra")
        audit.apply_tiers([stack])
        self.assertEqual(stack["tier"], audit.TIER_DEPLOYMENT)

    def test_a_cloudformation_template_bucket_is_deployment(self):
        """The AWS-generated-name rule, and its reason names CloudFormation."""
        bucket = make_row("S3Bucket", "cf-templates-a1b2c3d4e5f6g-us-east-1",
                          region="global")
        audit.apply_tiers([bucket])
        self.assertEqual(bucket["tier"], audit.TIER_DEPLOYMENT)
        self.assertIn("CloudFormation", bucket["why_tier"])

    def test_the_template_bucket_rule_accepts_any_region_format(self):
        """Partitions with longer region names match too."""
        for name in ("cf-templates-a1b2c3d4e5f6g-us-gov-west-1",
                     "cf-templates-a1b2c3d4e5f6g-cn-north-1",
                     "cf-templates-a1b2c3d4e5f6g-ap-southeast-4"):
            bucket = make_row("S3Bucket", name, region="global")
            audit.apply_tiers([bucket])
            self.assertEqual(bucket["tier"], audit.TIER_DEPLOYMENT, name)

    def test_runtime_from_an_incoming_request_path_link(self):
        api = make_row("APIGatewayRestApi", "rest1", "orders-api")
        fn = make_row("LambdaFunction", "orders-worker")
        audit.add_edge(api, "orders-worker", "integrates", "REST API integration URI",
                       conn_type="apigateway.lambda.integration",
                       target_service="LambdaFunction")
        audit.resolve_edges([api, fn])
        audit.apply_tiers([api, fn])
        self.assertEqual(fn["tier"], audit.TIER_RUNTIME)
        self.assertIn("request path", fn["why_tier"])
        # The API is runtime by default, not via the request-path rule.
        self.assertEqual(api["tier"], audit.TIER_RUNTIME)
        self.assertEqual(api["why_tier"], audit.DEFAULT_WHY_TIER)


class TierAbsenceTests(unittest.TestCase):
    """Without deployment evidence a row gets the assumed-runtime default."""

    def test_no_evidence_defaults_to_assumed_runtime(self):
        rows = [make_row("EC2Instance", "i-0abc"), make_row("S3Bucket", "logs",
                                                            region="global")]
        counts = audit.apply_tiers(rows)
        self.assertEqual([r["tier"] for r in rows], [audit.TIER_RUNTIME] * 2)
        self.assertEqual([r["why_tier"] for r in rows], [audit.DEFAULT_WHY_TIER] * 2)
        self.assertEqual(counts, {audit.TIER_RUNTIME: 2})

    def test_a_dangling_reference_does_not_classify_via_the_link_rule(self):
        """A dangling reference does not fire the link rule."""
        api = make_row("APIGatewayRestApi", "rest1", "orders-api")
        audit.add_edge(api, "not-in-this-scan", "integrates", "integration URI",
                       conn_type="apigateway.lambda.integration",
                       target_service="LambdaFunction")
        rows = [api]
        audit.resolve_edges(rows)
        audit.apply_tiers(rows)
        self.assertEqual(api["tier"], audit.TIER_RUNTIME)
        self.assertEqual(api["why_tier"], audit.DEFAULT_WHY_TIER)

    def test_an_unrelated_link_type_does_not_imply_runtime_via_the_link_rule(self):
        """Only the declared request-path mechanisms fire the link rule."""
        fn = make_row("LambdaFunction", "one-off-migration")
        logs = make_row("CloudWatchLogGroup", "/aws/lambda/one-off-migration")
        audit.add_edge(fn, "/aws/lambda/one-off-migration", "logs to",
                       "Lambda LoggingConfig",
                       conn_type="lambda.loggroup.logging-config",
                       target_service="CloudWatchLogGroup")
        rows = [fn, logs]
        audit.resolve_edges(rows)
        audit.apply_tiers(rows)
        self.assertEqual(logs["tier"], audit.TIER_RUNTIME)
        self.assertEqual(logs["why_tier"], audit.DEFAULT_WHY_TIER)

    def test_a_name_a_person_could_have_chosen_is_not_a_managed_bucket(self):
        """Person-chosen cf-templates-* names do not match the AWS format."""
        for name in ("cf-templates-my-team-us-east-1",   # hyphen in the middle
                     "cf-templates-a1b2c3d4e5f6g",       # no region suffix
                     "cf-templates-abc-us-east-1",       # middle too short
                     "my-cf-templates-a1b2c3d4e5f6g-us-east-1"):  # not anchored
            bucket = make_row("S3Bucket", name, region="global")
            audit.apply_tiers([bucket])
            self.assertEqual(bucket["tier"], audit.TIER_RUNTIME, name)
            self.assertEqual(bucket["why_tier"], audit.DEFAULT_WHY_TIER, name)

    def test_the_managed_bucket_rule_only_reads_buckets(self):
        """An S3 bucket name is globally unique, so the rule may match on
        resource_id - but only for the type that owns that namespace."""
        fn = make_row("LambdaFunction", "cf-templates-a1b2c3d4e5f6g-us-east-1")
        audit.apply_tiers([fn])
        self.assertEqual(fn["tier"], audit.TIER_RUNTIME)
        self.assertEqual(fn["why_tier"], audit.DEFAULT_WHY_TIER)

    def test_stated_evidence_beats_inferred_evidence(self):
        """Amplify's stated artifact bucket stays deployment even when linked to."""
        app = make_row("AmplifyApp", "d111", "bakery")
        app["_amplify_deployment_artifacts"] = ["bakery-deploy"]
        fn = make_row("LambdaFunction", "bakery-deploy")   # same id, other type
        app["_amplify_provenance"] = {}
        bucket = make_row("S3Bucket", "bakery-deploy", region="global")
        audit.add_edge(fn, "bakery-deploy", "dead letter", "DeadLetterConfig",
                       conn_type="lambda.any.dead-letter-config",
                       target_service="S3Bucket")
        rows = [app, fn, bucket]
        audit.resolve_edges(rows)
        audit.apply_tiers(rows)
        self.assertEqual(bucket["tier"], audit.TIER_DEPLOYMENT)
        self.assertIn("artifact store", bucket["why_tier"])


    def test_a_stated_fact_beats_the_generated_name(self):
        """The name rule fires last: Amplify's statement wins the why_tier."""
        app = make_row("AmplifyApp", "d111", "bakery")
        app["_amplify_deployment_artifacts"] = ["cf-templates-a1b2c3d4e5f6g-us-east-1"]
        bucket = make_row("S3Bucket", "cf-templates-a1b2c3d4e5f6g-us-east-1",
                          region="global")
        audit.apply_tiers([app, bucket])
        self.assertEqual(bucket["tier"], audit.TIER_DEPLOYMENT)
        self.assertIn("bakery", bucket["why_tier"])


class TierIsNotAGroupTests(unittest.TestCase):
    """The tier never changes project_group."""

    def test_tiers_do_not_change_project_group(self):
        app = make_row("AmplifyApp", "d111", "bakery", tags={"Project": "bakery"})
        app["_amplify_deployment_artifacts"] = ["bakery-deploy"]
        bucket = make_row("S3Bucket", "bakery-deploy", region="global",
                          tags={"Project": "bakery"})
        table = make_row("DynamoDBTable", "bakery-stores", tags={"Project": "bakery"})
        rows = [app, bucket, table]
        edge_links = audit.resolve_edges(rows)
        audit.apply_tiers(rows)
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            audit.apply_project_grouping(rows, edge_links=edge_links)
        self.assertEqual(bucket["tier"], audit.TIER_DEPLOYMENT)
        self.assertEqual({r["project_group"] for r in rows}, {"bakery"})


class TierSchemaTests(unittest.TestCase):

    def test_tier_fields_are_public_and_survive_the_snapshot(self):
        """tier and why_tier are in ROW_FIELDS and SNAPSHOT_ROW_FIELDS."""
        for field in ("tier", "why_tier"):
            self.assertIn(field, audit.ROW_FIELDS)
            self.assertIn(field, audit.SNAPSHOT_ROW_FIELDS)

    def test_a_new_row_starts_unclassified(self):
        row = make_row("EC2Instance", "i-0abc")
        self.assertEqual((row["tier"], row["why_tier"]), ("", ""))

    def test_a_snapshot_predating_the_tier_fields_is_refused(self):
        """An old snapshot without these fields is refused with a sentence, not a KeyError."""
        import json
        import tempfile

        # Version 1 predates both the tier fields and the identity fields, and
        # no upgrade step claims to know how to reconstruct them.
        stale = {"schema_version": 1, "rows": []}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "aws_scan.json")
            with open(path, "w") as handle:
                json.dump(stale, handle)
            with self.assertRaises(audit.AuditError) as caught:
                audit.read_snapshot(path)
        message = str(caught.exception)
        self.assertIn("aws_resource_audit.py", message)
        self.assertIn(str(audit.SCHEMA_VERSION), message)

    def test_a_version_2_snapshot_is_upgraded_rather_than_refused(self):
        """A v2 snapshot is upgraded; fields it cannot know stay unknown."""
        import json
        import tempfile

        old = {
            "schema_version": 2,
            "account": "111122223333",
            "rows": [{name: "" for name in audit.SNAPSHOT_ROW_FIELDS
                      if name not in ("arn", "account", "partition",
                                      "resource_key", "_references")}],
        }
        old["rows"][0].update({"service": "S3Bucket", "region": "global",
                               "resource_id": "assets", "_references": []})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "aws_scan.json")
            with open(path, "w") as handle:
                json.dump(old, handle)
            snapshot = audit.read_snapshot(path)

        row = snapshot.rows[0]
        self.assertEqual(row["arn"], "", "an ARN cannot be reconstructed")
        self.assertEqual(row["account"], "111122223333",
                         "the account is stated by the file itself")
        self.assertEqual(row["resource_key"],
                         "aws:S3Bucket:111122223333:global:assets")


class AmplifyProvenanceCollectionTests(unittest.TestCase):
    """The collect half: the two fields that were being read and discarded."""

    def _session(self, envs, stack_resources):
        return FakeSession({
            "amplify": {
                "list_backend_environments": {"backendEnvironments": envs},
                "get_app": {"app": {"environmentVariables": {}}},
                "list_branches": {"branches": []},
            },
            "cloudformation": {"list_stack_resources": stack_resources},
        })

    def test_deployment_artifacts_bucket_is_captured(self):
        session = self._session(
            [{"stackName": "amplify-bakery-dev",
              "deploymentArtifacts": "amplify-bakery-dev-deployment"}],
            {"StackResourceSummaries": []})
        backend = audit.find_amplify_backend_resources(session, "us-east-1", "d111")
        self.assertEqual(backend.deployment_artifacts, {"amplify-bakery-dev-deployment"})

    def test_provenance_records_logical_id_and_outermost_category(self):
        """The outermost nested stack's logical id is the category, through deeper nesting."""
        # Per-stack answers, so a resource cannot land under the wrong category.
        by_stack = {
            # The root stack: one nested category stack, plus a resource of
            # its own that therefore has no category.
            "amplify-bakery-dev": [
                {"ResourceType": "AWS::CloudFormation::Stack",
                 "PhysicalResourceId": "arn:stack/auth",
                 "LogicalResourceId": "auth"},
                {"ResourceType": "AWS::S3::Bucket",
                 "PhysicalResourceId": "bakery-deployment",
                 "LogicalResourceId": "DeploymentBucket"},
            ],
            # Inside "auth": another nesting level, so the category has to be
            # carried down rather than re-read at each level.
            "arn:stack/auth": [
                {"ResourceType": "AWS::CloudFormation::Stack",
                 "PhysicalResourceId": "arn:stack/auth-inner",
                 "LogicalResourceId": "innerthing"},
            ],
            "arn:stack/auth-inner": [
                {"ResourceType": "AWS::Cognito::UserPool",
                 "PhysicalResourceId": "us-east-1_pool",
                 "LogicalResourceId": "UserPool"},
            ],
        }
        session = self._session(
            [{"stackName": "amplify-bakery-dev"}],
            lambda **kw: {"StackResourceSummaries": by_stack[kw["StackName"]]})
        backend = audit.find_amplify_backend_resources(session, "us-east-1", "d111")

        self.assertEqual(backend.provenance["bakery-deployment"],
                         {"logical_id": "DeploymentBucket", "category": "",
                          "type": "AWS::S3::Bucket"})
        pool = backend.provenance["us-east-1_pool"]
        self.assertEqual(pool["logical_id"], "UserPool")
        self.assertEqual(pool["category"], "auth")

    def test_provenance_reaches_the_amplify_row(self):
        """apply_amplify_api_links is what puts the facts where apply_tiers
        looks for them; without this the two halves never meet."""
        session = self._session(
            [{"stackName": "amplify-bakery-dev",
              "deploymentArtifacts": "bakery-deployment"}],
            {"StackResourceSummaries": [
                {"ResourceType": "AWS::S3::Bucket",
                 "PhysicalResourceId": "bakery-deployment",
                 "LogicalResourceId": "DeploymentBucket"},
            ]})
        app = make_row("AmplifyApp", "d111", "bakery")
        bucket = make_row("S3Bucket", "bakery-deployment", region="global")
        rows = [app, bucket]
        audit.apply_amplify_api_links(rows, session)
        audit.apply_tiers(rows)
        self.assertEqual(bucket["tier"], audit.TIER_DEPLOYMENT)


class TierRenderingTests(unittest.TestCase):
    """The architecture diagram leaves deployment resources out instead of
    compartmenting them; the Graph tab still compartments."""

    def test_a_mixed_project_draws_one_box_and_only_its_runtime_half(self):
        rows = []
        for index, tier in enumerate([audit.TIER_RUNTIME, audit.TIER_DEPLOYMENT]):
            row = make_row("LambdaFunction", f"fn{index}", f"fn{index}")
            row["project_id"], row["project_group"], row["tier"] = "p-bakery", "bakery", tier
            rows.append(row)
        graph = {"nodes": [{"id": audit.member_key(r), "label": r["name"],
                            "service": r["service"], "kind": "resource"} for r in rows],
                 "edges": []}
        out = audit.render_mermaid(audit.architecture_graph(graph, rows), rows)
        self.assertEqual(re.findall(r"subgraph \S+\[\"([^\"]+)", out), ["bakery"])
        self.assertIn("fn0", out)
        self.assertNotIn("fn1", out)


if __name__ == "__main__":
    unittest.main()
