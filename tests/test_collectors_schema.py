"""Cross-cutting collector guards: every type is classified, tested and degrades to
an ERROR row when denied; collector order; the --debug Lambda diagnostic."""

import contextlib
import io
import pathlib
import unittest

from .fakes import (
    METRICS_DENIED,
    NO_METRICS,
    FakeSession,
    aws_ts,
    client_error,
    metrics_at,
    audit,
    recent,
)
from aws_resource_audit import console
from . import test_collectors_security
from .collectors_base import ACCOUNT, REGION, by_service, only


class SchemaAndFailureTests(unittest.TestCase):

    ALL_SERVICES = [
        "EC2Instance", "EBSVolume", "EBSSnapshot", "AMI", "ReservedInstance", "ElasticIP",
        "NetworkInterface", "SecurityGroup", "KeyPair", "LaunchTemplate", "SpotInstanceRequest",
        "LoadBalancer", "RDSInstance", "DynamoDBTable", "S3Bucket", "CloudWatchLogGroup",
        "LambdaFunction", "APIGatewayRestApi", "APIGatewayV2Api", "CloudWatchAlarm",
        "CloudTrailTrail", "AmplifyApp", "CognitoUserPool", "XRayGroup", "XRaySamplingRule",
        "CloudFormationStack", "IAMUser", "IAMRole", "IAMGroup", "IAMPolicy",
        "IAMRoleUnusedAccessFinding", "SQSQueue", "SNSTopic", "ElasticBeanstalkEnvironment",
        "ElasticBeanstalkApplication", "EventBridgeRule", "EventBridgeSchedule",
        "VPC", "Subnet", "RouteTable", "InternetGateway", "NatGateway", "VpcEndpoint",
        "RDSCluster", "RDSSnapshot", "EFSFileSystem", "ElastiCacheCluster", "OpenSearchDomain",
        "KMSKey", "Secret", "Route53HostedZone", "CloudFrontDistribution", "ECRRepository",
        "AutoScalingGroup", "TargetGroup", "StepFunctionsStateMachine", "ECSCluster", "ECSService",
        "CodeBuildProject", "CodePipeline", "BackupVault", "BackupPlan",
        "DocumentDBCluster", "DocumentDBInstance", "NeptuneCluster", "NeptuneInstance",
        "SageMakerEndpoint", "SageMakerNotebook", "EKSCluster", "RedshiftCluster",
        "EventBridgeBus",  # emitted by collect_eventbridge_rules, tested in test_connections
        "WAFWebACL", "NetworkFirewall", "APIGatewayDomainName",
        "RedshiftServerlessWorkgroup", "SageMakerStudioApp", "SageMakerTrainingJob",
        "KinesisStream", "FirehoseStream", "MSKCluster", "GlueJob", "GlueCrawler",
        "TransitGateway", "VPNConnection", "ACMCertificate", "SSMParameter", "AppSyncApi",
        "OpenSearchServerlessCollection", "Route53ResolverEndpoint",
    ]

    def test_collector_order_is_validated_at_import(self):
        """EC2 usage producers must run before consumers; a bad order raises."""
        self.assertTrue(audit.validate_collector_order())

        # An order where AMIs (which consume image_ids) run before the
        # instances that produce them must be rejected.
        broken = [
            ("AMIs", audit.collect_amis),
            ("EC2 instances", audit.collect_ec2_instances),
        ]
        with self.assertRaises(RuntimeError) as ctx:
            audit.validate_collector_order(broken)
        self.assertIn("image_ids", str(ctx.exception))
        self.assertIn("collect_amis", str(ctx.exception))

    def test_every_usage_registry_key_is_declared_in_the_flow_table(self):
        """Every registry key (except "complete", which has no ordering) is in
        EC2_USAGE_FLOW, so it is checked."""
        buckets = set(audit.new_ec2_usage_registry()) - {"complete"}
        self.assertEqual(
            buckets, set(audit.EC2_USAGE_FLOW),
            "EC2_USAGE_FLOW is out of sync with new_ec2_usage_registry()")

    def test_error_row_carries_the_same_schema_as_a_real_row(self):
        """error_row is built through new_row precisely so these can't drift."""
        real = audit.new_row("EC2Instance", REGION, "i-1", "", "", "", "", None,
                             True, "ACTIVE", "")
        err = audit.error_row("EC2Instance", REGION, "ERROR", "denied")
        self.assertEqual(set(real), set(err))
        self.assertIn("lookup failed", err["risk_if_removed"])

    def test_every_resource_type_has_a_billing_classification(self):
        """An unclassified type silently reports billing='unknown', which makes
        the cleanup triage summary wrong rather than obviously broken."""
        for service in self.ALL_SERVICES:
            self.assertIn(service, audit.SERVICE_BILLING, service)

    def test_every_resource_type_is_classified_for_the_graph(self):
        """Every type is explicitly structural or supporting, exactly once."""
        structural = audit.STRUCTURAL_SERVICES
        supporting = audit.SUPPORTING_SERVICES
        self.assertEqual(structural & supporting, frozenset(),
                         "a type cannot be both app structure and plumbing")
        self.assertEqual(structural | supporting, set(self.ALL_SERVICES),
                         "STRUCTURAL_SERVICES + SUPPORTING_SERVICES must cover "
                         "exactly the types the collectors emit")

    def test_structural_types_are_all_coloured_in_the_report(self):
        """Every drawn type has its own colour, not the grey default."""
        for service in audit.STRUCTURAL_SERVICES:
            self.assertIn(service, audit.SERVICE_META, service)

    def test_billing_classifications_use_the_documented_vocabulary(self):
        for service, (model, note) in audit.SERVICE_BILLING.items():
            self.assertIn(model, ("cost", "usage", "indirect", "free"), service)
            self.assertTrue(note.strip(), service)

    def test_every_resource_type_has_a_collector_test(self):
        # Not this file: ALL_SERVICES itself names every type, so reading it proves nothing.
        here = pathlib.Path(__file__).parent
        source = "".join(p.read_text() for pattern in ("test_collectors_*.py", "test_connections_*.py")
                         for p in here.glob(pattern) if p.name != pathlib.Path(__file__).name)
        missing = [s for s in self.ALL_SERVICES if s not in source]
        self.assertEqual(missing, [], f"resource types with no test: {missing}")

    def test_denied_api_call_produces_an_error_row_not_an_empty_result(self):
        """The script's core promise: a failed lookup is reported, never
        silently rendered as "nothing here" (which reads as "safe to delete")."""
        cases = [
            (audit.collect_ec2_instances, "ec2", "describe_instances", "EC2Instance"),
            (audit.collect_ebs_volumes, "ec2", "describe_volumes", "EBSVolume"),
            (audit.collect_elastic_ips, "ec2", "describe_addresses", "ElasticIP"),
            (audit.collect_launch_templates, "ec2", "describe_launch_templates", "LaunchTemplate"),
            (audit.collect_spot_instance_requests, "ec2", "describe_spot_instance_requests",
             "SpotInstanceRequest"),
            (audit.collect_reserved_instances, "ec2", "describe_reserved_instances",
             "ReservedInstance"),
            (audit.collect_amis, "ec2", "describe_images", "AMI"),
            (audit.collect_ebs_snapshots, "ec2", "describe_snapshots", "EBSSnapshot"),
            (audit.collect_security_groups, "ec2", "describe_security_groups", "SecurityGroup"),
            (audit.collect_key_pairs, "ec2", "describe_key_pairs", "KeyPair"),
            (audit.collect_network_interfaces, "ec2", "describe_network_interfaces",
             "NetworkInterface"),
            (audit.collect_vpcs, "ec2", "describe_vpcs", "VPC"),
            (audit.collect_subnets, "ec2", "describe_subnets", "Subnet"),
            (audit.collect_route_tables, "ec2", "describe_route_tables", "RouteTable"),
            (audit.collect_internet_gateways, "ec2", "describe_internet_gateways",
             "InternetGateway"),
            (audit.collect_nat_gateways, "ec2", "describe_nat_gateways", "NatGateway"),
            (audit.collect_vpc_endpoints, "ec2", "describe_vpc_endpoints", "VpcEndpoint"),
            (audit.collect_resolver_endpoints, "route53resolver", "list_resolver_endpoints",
             "Route53ResolverEndpoint"),
            (audit.collect_transit_gateways, "ec2", "describe_transit_gateways", "TransitGateway"),
            (audit.collect_vpn_connections, "ec2", "describe_vpn_connections", "VPNConnection"),
            (audit.collect_rds_instances, "rds", "describe_db_instances", "RDSInstance"),
            (audit.collect_rds_clusters, "rds", "describe_db_clusters", "RDSCluster"),
            (audit.collect_efs_file_systems, "efs", "describe_file_systems", "EFSFileSystem"),
            (audit.collect_elasticache_clusters, "elasticache", "describe_cache_clusters",
             "ElastiCacheCluster"),
            (audit.collect_opensearch_serverless_collections, "opensearchserverless", "list_collections",
             "OpenSearchServerlessCollection"),
            (audit.collect_opensearch_domains, "opensearch", "list_domain_names",
             "OpenSearchDomain"),
            (audit.collect_kms_keys, "kms", "list_keys", "KMSKey"),
            (audit.collect_secrets, "secretsmanager", "list_secrets", "Secret"),
            (audit.collect_acm_certificates, "acm", "list_certificates", "ACMCertificate"),
            (audit.collect_ssm_parameters, "ssm", "describe_parameters", "SSMParameter"),
            (audit.collect_appsync_apis, "appsync", "list_graphql_apis", "AppSyncApi"),
            (audit.collect_route53_hosted_zones, "route53", "list_hosted_zones",
             "Route53HostedZone"),
            (audit.collect_cloudfront_distributions, "cloudfront", "list_distributions",
             "CloudFrontDistribution"),
            (audit.collect_ecr_repositories, "ecr", "describe_repositories", "ECRRepository"),
            (audit.collect_auto_scaling_groups, "autoscaling", "describe_auto_scaling_groups",
             "AutoScalingGroup"),
            (audit.collect_target_groups, "elbv2", "describe_target_groups", "TargetGroup"),
            (audit.collect_state_machines, "stepfunctions", "list_state_machines",
             "StepFunctionsStateMachine"),
            (audit.collect_ecs, "ecs", "list_clusters", "ECSCluster"),
            (audit.collect_codebuild_projects, "codebuild", "list_projects", "CodeBuildProject"),
            (audit.collect_codepipelines, "codepipeline", "list_pipelines", "CodePipeline"),
            (audit.collect_backup_vaults, "backup", "list_backup_vaults", "BackupVault"),
            (audit.collect_backup_plans, "backup", "list_backup_plans", "BackupPlan"),
            (audit.collect_sagemaker_endpoints, "sagemaker", "list_endpoints", "SageMakerEndpoint"),
            (audit.collect_eks_clusters, "eks", "list_clusters", "EKSCluster"),
            (audit.collect_redshift_clusters, "redshift", "describe_clusters", "RedshiftCluster"),
            (audit.collect_redshift_serverless_workgroups, "redshift-serverless", "list_workgroups",
             "RedshiftServerlessWorkgroup"),
            (audit.collect_waf_web_acls, "wafv2", "list_web_acls", "WAFWebACL"),
            (audit.collect_network_firewalls, "network-firewall", "list_firewalls", "NetworkFirewall"),
            (audit.collect_sagemaker_studio_apps, "sagemaker", "list_apps", "SageMakerStudioApp"),
            (audit.collect_sagemaker_training_jobs, "sagemaker", "list_training_jobs",
             "SageMakerTrainingJob"),
            (audit.collect_sagemaker_notebooks, "sagemaker", "list_notebook_instances",
             "SageMakerNotebook"),
            (audit.collect_lambda_functions, "lambda", "list_functions", "LambdaFunction"),
            (audit.collect_load_balancers, "elbv2", "describe_load_balancers", "LoadBalancer"),
            (audit.collect_classic_load_balancers, "elb", "describe_load_balancers", "LoadBalancer"),
            (audit.collect_dynamodb_tables, "dynamodb", "list_tables", "DynamoDBTable"),
            (audit.collect_sqs_queues, "sqs", "list_queues", "SQSQueue"),
            (audit.collect_kinesis_streams, "kinesis", "list_streams", "KinesisStream"),
            (audit.collect_firehose_streams, "firehose", "list_delivery_streams", "FirehoseStream"),
            (audit.collect_msk_clusters, "kafka", "list_clusters_v2", "MSKCluster"),
            (audit.collect_glue_jobs, "glue", "get_jobs", "GlueJob"),
            (audit.collect_glue_crawlers, "glue", "get_crawlers", "GlueCrawler"),
            (audit.collect_sns_topics, "sns", "list_topics", "SNSTopic"),
            (audit.collect_api_gateway_rest_apis, "apigateway", "get_rest_apis",
             "APIGatewayRestApi"),
            (audit.collect_api_gateway_v2_apis, "apigatewayv2", "get_apis", "APIGatewayV2Api"),
            (audit.collect_api_gateway_domain_names, "apigateway", "get_domain_names",
             "APIGatewayDomainName"),
            (audit.collect_cloudtrail_trails, "cloudtrail", "describe_trails", "CloudTrailTrail"),
            (audit.collect_cloudwatch_log_groups, "logs", "describe_log_groups",
             "CloudWatchLogGroup"),
            (audit.collect_cloudwatch_alarms, "cloudwatch", "describe_alarms", "CloudWatchAlarm"),
            (audit.collect_amplify_apps, "amplify", "list_apps", "AmplifyApp"),
            (audit.collect_cloudformation_stacks, "cloudformation", "list_stacks",
             "CloudFormationStack"),
            (audit.collect_elastic_beanstalk_environments, "elasticbeanstalk",
             "describe_environments", "ElasticBeanstalkEnvironment"),
            (audit.collect_cognito_user_pools, "cognito-idp", "list_user_pools",
             "CognitoUserPool"),
            (audit.collect_access_analyzer_unused_roles, "accessanalyzer", "list_analyzers",
             "IAMRoleUnusedAccessFinding"),
        ]
        for collector, service_name, operation, expected_service in cases:
            with self.subTest(service=expected_service):
                session = FakeSession({service_name: {operation: client_error(operation=operation)}})
                rows = collector(session, REGION)
                self.assertTrue(rows, f"{expected_service}: denial produced no row at all")
                row = rows[0]
                self.assertEqual(row["service"], expected_service)
                self.assertEqual(row["flag"], "ERROR")
                self.assertIn("AccessDenied", row["notes"])
                self.assertIn("UNKNOWN", row["risk_if_removed"],
                              "a failed lookup must never read as a safe deletion")

    def test_each_iam_list_is_denied_independently(self):
        """IAM's four list calls are permissioned separately; one denial must not abort
        the scan."""
        for operation, service in [("list_users", "IAMUser"),
                                   ("list_groups", "IAMGroup"),
                                   ("list_policies", "IAMPolicy"),
                                   ("list_roles", "IAMRole")]:
            with self.subTest(operation=operation):
                session = test_collectors_security.IamCollectorTests._session(**{operation: client_error(operation=operation)})
                rows = audit.collect_iam(session, used_role_names=set())
                row = only(by_service(rows, service))
                self.assertEqual(row["flag"], "ERROR")
                self.assertIn("AccessDenied", row["notes"])
                self.assertIn("UNKNOWN", row["risk_if_removed"])

    def test_one_denied_iam_list_does_not_hide_the_others(self):
        session = test_collectors_security.IamCollectorTests._session(
            list_users=client_error(operation="ListUsers"),
            list_roles={"Roles": [{"RoleName": "worker", "CreateDate": recent(500),
                                   "RoleLastUsed": {"LastUsedDate": recent(2)}}]},
        )
        rows = audit.collect_iam(session, used_role_names={"worker"})
        self.assertEqual(only(by_service(rows, "IAMUser"))["flag"], "ERROR")
        self.assertEqual(only(by_service(rows, "IAMRole"))["flag"], "ACTIVE",
                         "a denial on one IAM list must not cost the rest of the report")

    def test_xray_reports_each_half_independently(self):
        """get_groups and get_sampling_rules are separate calls; one failing
        must not hide the other's results."""
        session = FakeSession({"xray": {
            "get_groups": client_error(operation="GetGroups"),
            "get_sampling_rules": {"SamplingRuleRecords": []},
        }})
        rows = audit.collect_xray_config(session, REGION)
        self.assertEqual(only(by_service(rows, "XRayGroup"))["flag"], "ERROR")

    def test_rds_snapshot_denial_is_reported_per_kind(self):
        """Two lookups, so one denied must not hide the other kind's snapshots."""
        session = FakeSession({"rds": {
            "describe_db_snapshots": client_error(operation="describe_db_snapshots"),
            "describe_db_cluster_snapshots": {"DBClusterSnapshots": [{
                "DBClusterSnapshotIdentifier": "final", "DBClusterIdentifier": "c",
                "SnapshotCreateTime": recent()}]},
        }})
        rows = audit.collect_rds_snapshots(session, REGION)
        self.assertEqual([r["flag"] for r in rows][0], "ERROR")
        self.assertIn("AccessDenied", rows[0]["notes"])
        self.assertEqual(rows[1]["resource_id"], "final")

    def test_s3_denial_produces_an_error_row(self):
        session = FakeSession({"s3": {"list_buckets": client_error(operation="ListBuckets")}})
        row = only(audit.collect_s3_buckets(session))
        self.assertEqual(row["service"], "S3Bucket")
        self.assertEqual(row["flag"], "ERROR")

    def test_cloudwatch_failure_is_never_reported_as_no_activity(self):
        """The single most dangerous confusion in the whole script: a denied
        metric lookup must be labelled as such, not rendered as "idle"."""
        session = FakeSession({
            "ec2": {"describe_instances": {"Reservations": [{"Instances": [{
                "InstanceId": "i-0abc", "State": {"Name": "running"}, "LaunchTime": recent(),
            }]}]}},
            "cloudwatch": METRICS_DENIED,
        })
        row = only(audit.collect_ec2_instances(session, REGION))
        self.assertIn("Could not read CPUUtilization", row["notes"])
        self.assertIn("UNKNOWN here, not idle", row["notes"])
        self.assertIn("AccessDenied", row["notes"])

    def test_every_row_carries_the_full_output_schema(self):
        """Any missing key would surface as a blank column or a KeyError during
        CSV writing, depending on which writer path ran."""
        required = {
            "service", "region", "resource_id", "name", "created", "last_used",
            "last_used_days", "inferred", "flag", "notes", "tags", "description",
            "connections", "billing", "billing_note", "risk_if_removed",
            "est_monthly_cost_usd", "cost_notes", "project_group", "grouping_method",
            "why_grouped",
        }
        session = FakeSession({
            "ec2": {"describe_instances": {"Reservations": [{"Instances": [{
                "InstanceId": "i-0abc", "State": {"Name": "running"}, "LaunchTime": recent(),
            }]}]}},
            "cloudwatch": NO_METRICS,
        })
        rows = audit.collect_ec2_instances(session, REGION)
        rows.append(audit.error_row("EC2Instance", REGION, "ERROR", "denied"))
        for row in rows:
            self.assertEqual(required - set(row), set(), f"missing keys in {row['resource_id']}")


class DebugLambdaEnvTests(unittest.TestCase):
    """--debug reaches the Lambda collector, which must read console.VERBOSE as a module
    attribute (an imported copy would stay False)."""

    def _run(self, debug):
        session = FakeSession({
            "lambda": {
                "list_event_source_mappings": {"EventSourceMappings": []},
                "list_functions": {"Functions": [{
                    "FunctionName": "orders-fn",
                    "FunctionArn": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:orders-fn",
                    "Runtime": "python3.11", "LastModified": aws_ts(recent()),
                    "Environment": {"Variables": {"TABLE": "orders-table"}},
                }]},
                "list_tags": {"Tags": {}},
                "list_aliases": {"Aliases": []},
            },
            "cloudwatch": metrics_at(recent()),
        })
        original = console.VERBOSE
        console.VERBOSE = debug
        try:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                audit.collect_lambda_functions(session, REGION)
            return buffer.getvalue()
        finally:
            console.VERBOSE = original

    def test_on_prints_each_env_var_and_what_was_extracted(self):
        output = self._run(True)
        self.assertIn("[explain]", output)
        self.assertIn("TABLE", output)
        self.assertIn("orders-table", output,
                      "the diagnostic must show the value, not just the key")

    def test_off_prints_nothing(self):
        self.assertEqual(self._run(False), "")


if __name__ == "__main__":
    unittest.main()
