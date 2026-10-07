"""Collector tests for compute and deployment: Lambda, ECS, EKS, Step Functions,
SageMaker, ECR, CodeBuild, CodePipeline, Amplify, CloudFormation, Elastic Beanstalk."""

import unittest

from .fakes import (
    NO_METRICS,
    FakeSession,
    ancient,
    aws_ts,
    client_error,
    find_edge,
    metrics_all_zero,
    metrics_at,
    audit,
    recent,
)
from .collectors_base import ACCOUNT, REGION, only


class ContainerCollectorTests(unittest.TestCase):

    def test_an_ecr_repository_reads_its_newest_pull(self):
        session = FakeSession({"ecr": {
            "describe_repositories": {"repositories": [{
                "repositoryName": "api", "repositoryArn": "arn:aws:ecr:us-east-1:1:repository/api",
                "createdAt": ancient()}]},
            "describe_images": {"imageDetails": [
                {"imagePushedAt": ancient(), "lastRecordedPullTime": recent(),
                 "imageSizeInBytes": 2 * 1024 ** 3, "imageTags": ["v1"]},
                {"imagePushedAt": ancient(), "imageSizeInBytes": 1024 ** 3}]},
            "list_tags_for_resource": {"tags": []},
        }})
        row = only(audit.collect_ecr_repositories(session, REGION))
        self.assertEqual((row["service"], row["billing"]), ("ECRRepository", "cost"))
        self.assertIn("ACTIVE", row["flag"])
        self.assertIn("3.00GiB", row["description"])
        self.assertIn("1 untagged", row["notes"])

    def test_a_state_machine_reads_its_latest_execution(self):
        arn = f"arn:aws:states:{REGION}:1:stateMachine:orders"
        session = FakeSession({"stepfunctions": {
            "list_state_machines": {"stateMachines": [{
                "stateMachineArn": arn, "name": "orders", "type": "STANDARD",
                "creationDate": ancient()}]},
            "describe_state_machine": {"definition": "{}", "roleArn": ""},
            "list_executions": {"executions": [{"startDate": recent()}]},
            "list_tags_for_resource": {"tags": []},
        }})
        row = only(audit.collect_state_machines(session, REGION))
        self.assertEqual((row["service"], row["billing"]), ("StepFunctionsStateMachine", "usage"))
        self.assertIn("ACTIVE", row["flag"])

    def test_an_ecs_service_scaled_to_zero_and_its_cluster(self):
        session = FakeSession({"ecs": {
            "list_clusters": {"clusterArns": ["arn:aws:ecs:us-east-1:1:cluster/main"]},
            "describe_clusters": {"clusters": [{
                "clusterName": "main", "clusterArn": "arn:aws:ecs:us-east-1:1:cluster/main",
                "activeServicesCount": 1, "runningTasksCount": 0}]},
            "list_services": {"serviceArns": ["arn:aws:ecs:us-east-1:1:service/main/api"]},
            "describe_services": {"services": [{
                "serviceName": "api", "serviceArn": "arn:aws:ecs:us-east-1:1:service/main/api",
                "desiredCount": 0, "runningCount": 0, "launchType": "FARGATE",
                "createdAt": ancient(), "taskDefinition": "api:3"}]},
            "describe_task_definition": {"taskDefinition": {"containerDefinitions": []}},
        }, "elbv2": {}})
        rows = {r["resource_id"]: r for r in audit.collect_ecs(session, REGION)}
        self.assertIn("ACTIVE", rows["main"]["flag"])
        self.assertEqual(rows["main"]["billing"], "free")
        self.assertIn("scaled to zero", rows["main/api"]["flag"])
        self.assertEqual(rows["main/api"]["name"], "api")

    def test_a_build_project_reads_its_last_build(self):
        session = FakeSession({"codebuild": {
            "list_projects": {"projects": ["api-build"]},
            "batch_get_projects": {"projects": [{"name": "api-build", "created": ancient(),
                                                 "source": {"type": "GITHUB"}}]},
            "list_builds_for_project": {"ids": ["api-build:1"]},
            "batch_get_builds": {"builds": [{"endTime": recent()}]},
        }})
        row = only(audit.collect_codebuild_projects(session, REGION))
        self.assertEqual((row["service"], row["billing"]), ("CodeBuildProject", "usage"))
        self.assertIn("ACTIVE", row["flag"])

    def test_a_pipeline_that_never_ran_is_stale(self):
        session = FakeSession({"codepipeline": {
            "list_pipelines": {"pipelines": [{"name": "old", "created": ancient()}]},
            "list_pipeline_executions": {"pipelineExecutionSummaries": []},
            "get_pipeline": {"pipeline": {"stages": []}},
        }})
        self.assertIn("STALE", only(audit.collect_codepipelines(session, REGION))["flag"])

    def test_an_uncalled_sagemaker_endpoint_is_stale(self):
        session = FakeSession({"sagemaker": {
            "list_endpoints": {"Endpoints": [{"EndpointName": "churn", "CreationTime": ancient(),
                                              "EndpointStatus": "InService"}]},
            "describe_endpoint": {"ProductionVariants": [{"VariantName": "AllTraffic"}]},
        }, "cloudwatch": NO_METRICS})
        row = only(audit.collect_sagemaker_endpoints(session, REGION))
        self.assertEqual((row["service"], row["billing"]), ("SageMakerEndpoint", "cost"))
        self.assertIn("STALE", row["flag"])    # Invocations absent means nobody called it

    @staticmethod
    def _studio(*apps, last_activity=None):
        return FakeSession({"sagemaker": {
            "list_apps": {"Apps": list(apps)},
            "describe_app": {"AppArn": "arn:app", "LastUserActivityTimestamp": last_activity},
            "list_tags": {"Tags": []},
        }})

    def test_studio_apps_bill_only_off_the_system_size_and_deleted_ones_are_skipped(self):
        def app(name, size, status="InService"):
            return {"DomainId": "d-1", "UserProfileName": "alice", "AppType": "JupyterLab",
                    "AppName": name, "Status": status, "CreationTime": ancient(),
                    "ResourceSpec": {"InstanceType": size}}
        session = self._studio(app("ui", "system"), app("gpu", "ml.g4dn.xlarge"),
                               app("old", "ml.t3.medium", status="Deleted"), last_activity=ancient())
        rows = {r["resource_id"].split("/")[-1]: r for r in audit.collect_sagemaker_studio_apps(session, REGION)}
        self.assertEqual(set(rows), {"ui", "gpu"})
        self.assertEqual(rows["gpu"]["service"], "SageMakerStudioApp")
        self.assertIn("free", rows["ui"]["flag"])
        self.assertIn("STALE", rows["gpu"]["flag"])

    def test_training_jobs_are_read_from_the_cost_window_and_finished_ones_are_history(self):
        session = FakeSession({"sagemaker": {
            "list_training_jobs": {"TrainingJobSummaries": [
                {"TrainingJobName": "fit-1", "TrainingJobStatus": "Completed",
                 "CreationTime": recent(), "TrainingEndTime": recent()},
                {"TrainingJobName": "fit-2", "TrainingJobStatus": "InProgress", "CreationTime": recent()}]},
            "list_tags": {"Tags": []},
        }})
        rows = {r["resource_id"]: r for r in audit.collect_sagemaker_training_jobs(session, REGION)}
        self.assertEqual(rows["fit-1"]["service"], "SageMakerTrainingJob")
        self.assertTrue(rows["fit-1"]["flag"].startswith("HISTORY"))
        self.assertTrue(rows["fit-2"]["flag"].startswith("ACTIVE"))
        (_op, kwargs), = [c for c in session.get("sagemaker").calls if c[0] == "list_training_jobs"]
        self.assertIn("CreationTimeAfter", kwargs)

    def test_a_stopped_notebook_still_bills_storage(self):
        session = FakeSession({"sagemaker": {
            "list_notebook_instances": {"NotebookInstances": [{
                "NotebookInstanceName": "scratch", "NotebookInstanceStatus": "Stopped",
                "InstanceType": "ml.t3.medium", "CreationTime": ancient()}]},
            "describe_notebook_instance": {},
        }})
        row = only(audit.collect_sagemaker_notebooks(session, REGION))
        self.assertEqual(row["service"], "SageMakerNotebook")
        self.assertIn("storage volume still bills", row["flag"])

    def test_an_eks_cluster_with_no_nodes_is_flagged(self):
        session = FakeSession({"eks": {
            "list_clusters": {"clusters": ["idle"]},
            "describe_cluster": {"cluster": {"name": "idle", "version": "1.30", "createdAt": ancient()}},
            "list_nodegroups": {"nodegroups": []},
            "list_fargate_profiles": {"fargateProfileNames": []},
        }})
        row = only(audit.collect_eks_clusters(session, REGION))
        self.assertEqual((row["service"], row["billing"]), ("EKSCluster", "cost"))
        self.assertIn("control plane bills for nothing", row["flag"])


class ComputeCollectorTests(unittest.TestCase):

    def test_lambda_function(self):
        session = FakeSession({
            "lambda": {
                "list_event_source_mappings": {"EventSourceMappings": []},
                "list_functions": {"Functions": [{
                    "FunctionName": "orders-fn",
                    "FunctionArn": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:orders-fn",
                    "Role": f"arn:aws:iam::{ACCOUNT}:role/orders-role",
                    "Runtime": "python3.11", "LastModified": aws_ts(recent()),
                    "State": "Active",
                }]},
                "list_tags": {"Tags": {}},
                "list_aliases": {"Aliases": []},
            },
            "cloudwatch": metrics_at(recent()),
        })
        row = only(audit.collect_lambda_functions(session, REGION))
        self.assertEqual(row["service"], "LambdaFunction")
        self.assertEqual(row["billing"], "usage")
        self.assertIn("LastModified", row["notes"],
                      "Lambda has no true creation timestamp; that must be stated")

    def test_lambda_zero_invocations_is_distinguished_from_no_data(self):
        """"we looked and it was zero" is a real finding; "we couldn't look" is
        not - conflating them is the failure this script exists to avoid."""
        session = FakeSession({
            "lambda": {
                "list_event_source_mappings": {"EventSourceMappings": []},
                "list_functions": {"Functions": [{
                    "FunctionName": "idle-fn", "FunctionArn": "arn:aws:lambda:x:y:function:idle-fn",
                    "LastModified": aws_ts(ancient()), "Runtime": "python3.11",
                }]},
                "list_tags": {"Tags": {}}, "list_aliases": {"Aliases": []},
            },
            "cloudwatch": metrics_all_zero(recent()),
        })
        row = only(audit.collect_lambda_functions(session, REGION))
        self.assertNotIn("Could not read CloudWatch", row["notes"])
        self.assertIn("recorded ZERO activity", row["notes"])

    def test_amplify_app(self):
        session = FakeSession({"amplify": {
            "list_apps": {"apps": [{"appId": "d1234", "name": "orders-web",
                                    "createTime": recent(100), "platform": "WEB",
                                    "updateTime": recent(5)}]},
            "list_branches": {"branches": []},
        }})
        row = only(audit.collect_amplify_apps(session, REGION))
        self.assertEqual(row["service"], "AmplifyApp")
        self.assertEqual(row["resource_id"], "d1234")
        self.assertEqual(row["billing"], "usage")

    def test_cloudformation_stack(self):
        session = FakeSession({"cloudformation": {"list_stacks": {"StackSummaries": [
            {"StackName": "orders", "StackId": f"arn:aws:cloudformation:{REGION}:{ACCOUNT}:stack/orders/1",
             "StackStatus": "CREATE_COMPLETE", "CreationTime": recent(),
             "TemplateDescription": "orders backend"},
            {"StackName": "deleted", "StackStatus": "DELETE_COMPLETE", "CreationTime": recent()},
        ]}}})
        rows = audit.collect_cloudformation_stacks(session, REGION)
        self.assertEqual(len(rows), 1, "DELETE_COMPLETE stacks are gone, not resources")
        self.assertEqual(rows[0]["service"], "CloudFormationStack")
        self.assertEqual(rows[0]["billing"], "free")

    def test_cloudformation_failed_stack_is_flagged(self):
        session = FakeSession({"cloudformation": {"list_stacks": {"StackSummaries": [{
            "StackName": "broken", "StackId": "arn:x", "StackStatus": "ROLLBACK_FAILED",
            "CreationTime": recent(),
        }]}}})
        self.assertEqual(only(audit.collect_cloudformation_stacks(session, REGION))["flag"],
                         "STALE (FAILED STATE)")

    def test_elastic_beanstalk_environment(self):
        env_arn = f"arn:aws:elasticbeanstalk:{REGION}:{ACCOUNT}:environment/legacy-app/legacy-app-env"
        session = FakeSession({"elasticbeanstalk": {
            "describe_environments": {"Environments": [{
                "EnvironmentId": "e-abc123", "EnvironmentName": "legacy-app-env",
                "ApplicationName": "legacy-app", "VersionLabel": "v3",
                "EnvironmentArn": env_arn, "Status": "Ready", "Health": "Green",
                "DateCreated": ancient(1000), "DateUpdated": recent(10),
                "Description": "legacy web tier",
            }]},
            "list_tags_for_resource": {"ResourceTags": [{"Key": "Project", "Value": "legacy"}]},
            "describe_environment_resources": {"EnvironmentResources": {
                "Instances": [{"Id": "i-0abc123"}],
                "LoadBalancers": [{"Name": "legacy-alb"}],
            }},
            "describe_application_versions": {"ApplicationVersions": [{
                "SourceBundle": {"S3Bucket": "elasticbeanstalk-us-east-1-111122223333",
                                 "S3Key": "legacy-app/v3.zip"},
            }]},
        }})
        row = only(audit.collect_elastic_beanstalk_environments(session, REGION))
        self.assertEqual(row["service"], "ElasticBeanstalkEnvironment")
        self.assertEqual(row["resource_id"], "e-abc123")
        self.assertEqual(row["name"], "legacy-app-env")
        self.assertEqual(row["billing"], "indirect")
        self.assertEqual(row["tags"], {"Project": "legacy"})
        self.assertEqual(find_edge(row, "elasticbeanstalk.ec2instance.environment-resource")["target_id"],
                         "i-0abc123")
        self.assertEqual(find_edge(row, "elasticbeanstalk.loadbalancer.environment-resource")["target_id"],
                         "legacy-alb")
        self.assertEqual(find_edge(row, "elasticbeanstalk.s3bucket.deployment-artifact")["target_id"],
                         "elasticbeanstalk-us-east-1-111122223333")

    def test_elastic_beanstalk_terminating_environment_is_flagged(self):
        session = FakeSession({"elasticbeanstalk": {
            "describe_environments": {"Environments": [{
                "EnvironmentId": "e-dying", "EnvironmentName": "dying-env",
                "ApplicationName": "app", "Status": "Terminating",
                "DateCreated": recent(30), "DateUpdated": recent(1),
            }]},
            "list_tags_for_resource": {"ResourceTags": []},
            "describe_environment_resources": {"EnvironmentResources": {}},
        }})
        self.assertEqual(only(audit.collect_elastic_beanstalk_environments(session, REGION))["flag"],
                         "STALE (TERMINATING)")

    def test_elastic_beanstalk_application(self):
        app_arn = f"arn:aws:elasticbeanstalk:{REGION}:{ACCOUNT}:application/legacy-app"
        session = FakeSession({
            "elasticbeanstalk": {
                "describe_applications": {"Applications": [{
                    "ApplicationName": "legacy-app", "ApplicationArn": app_arn,
                    "DateCreated": ancient(1000), "DateUpdated": recent(10),
                    "Description": "the old web tier", "Versions": ["v1", "v2"],
                }]},
                "list_tags_for_resource": {"ResourceTags": [
                    {"Key": "Project", "Value": "legacy"}]},
                "describe_application_versions": {"ApplicationVersions": [{
                    "SourceBundle": {"S3Bucket": f"elasticbeanstalk-{REGION}-{ACCOUNT}",
                                     "S3Key": "legacy-app/v2.zip"},
                }]},
            },
            "s3": {"list_objects_v2": {"KeyCount": 1}},
        })
        row = only(audit.collect_elastic_beanstalk_applications(session, REGION))
        self.assertEqual(row["service"], "ElasticBeanstalkApplication")
        self.assertEqual(row["resource_id"], "legacy-app")
        self.assertEqual(row["billing"], "indirect")
        self.assertEqual(row["tags"], {"Project": "legacy"})
        self.assertIn("2 application version(s)", row["notes"])
        bucket = f"elasticbeanstalk-{REGION}-{ACCOUNT}"
        self.assertEqual(find_edge(row, "ebapplication.s3bucket.source-bundle")["target_id"],
                         bucket)
        self.assertEqual(find_edge(row, "ebapplication.s3bucket.service-bucket")["target_id"],
                         bucket)

    def test_elastic_beanstalk_application_bucket_probe_finds_nothing(self):
        """The service bucket exists but holds no folder for this application,
        so the derived name stays a derived name and no edge is created."""
        app_arn = f"arn:aws:elasticbeanstalk:{REGION}:{ACCOUNT}:application/lonely"
        session = FakeSession({
            "elasticbeanstalk": {
                "describe_applications": {"Applications": [{
                    "ApplicationName": "lonely", "ApplicationArn": app_arn,
                    "DateCreated": recent(40), "DateUpdated": recent(40),
                }]},
                "list_tags_for_resource": {"ResourceTags": []},
                "describe_application_versions": {"ApplicationVersions": []},
            },
            "s3": {"list_objects_v2": {"KeyCount": 0}},
        })
        row = only(audit.collect_elastic_beanstalk_applications(session, REGION))
        self.assertIsNone(find_edge(row, "ebapplication.s3bucket.service-bucket"))
        self.assertIn("No application versions", row["notes"])

    def test_elastic_beanstalk_application_denied_probe_is_reported_not_silent(self):
        """A denied ListObjectsV2 must not read as "the bucket holds nothing" -
        that is the difference between "no residue" and "we could not look"."""
        app_arn = f"arn:aws:elasticbeanstalk:{REGION}:{ACCOUNT}:application/legacy-app"
        session = FakeSession({
            "elasticbeanstalk": {
                "describe_applications": {"Applications": [{
                    "ApplicationName": "legacy-app", "ApplicationArn": app_arn,
                    "DateCreated": recent(40), "DateUpdated": recent(40),
                }]},
                "list_tags_for_resource": {"ResourceTags": []},
                "describe_application_versions": {"ApplicationVersions": []},
            },
            "s3": {"list_objects_v2": client_error("AccessDenied", "denied")},
        })
        row = only(audit.collect_elastic_beanstalk_applications(session, REGION))
        self.assertIsNone(find_edge(row, "ebapplication.s3bucket.service-bucket"))
        self.assertIn("Could not check", row["notes"])

    def test_elastic_beanstalk_application_without_an_arn_derives_no_bucket(self):
        """The account id comes from the application's own ARN. No ARN, no
        derived bucket name - never a guess at the account."""
        session = FakeSession({"elasticbeanstalk": {
            "describe_applications": {"Applications": [{
                "ApplicationName": "legacy-app",
                "DateCreated": recent(40), "DateUpdated": recent(40),
            }]},
            "describe_application_versions": {"ApplicationVersions": []},
        }})
        row = only(audit.collect_elastic_beanstalk_applications(session, REGION))
        self.assertIsNone(find_edge(row, "ebapplication.s3bucket.service-bucket"))

    def test_elastic_beanstalk_environment_names_its_application(self):
        session = FakeSession({"elasticbeanstalk": {
            "describe_environments": {"Environments": [{
                "EnvironmentId": "e-abc123", "EnvironmentName": "legacy-app-env",
                "ApplicationName": "legacy-app", "Status": "Ready",
                "DateCreated": recent(40), "DateUpdated": recent(5),
            }]},
            "list_tags_for_resource": {"ResourceTags": []},
            "describe_environment_resources": {"EnvironmentResources": {}},
        }})
        row = only(audit.collect_elastic_beanstalk_environments(session, REGION))
        edge = find_edge(row, "elasticbeanstalk.ebapplication.application-name")
        self.assertEqual(edge["target_id"], "legacy-app")


if __name__ == "__main__":
    unittest.main()
