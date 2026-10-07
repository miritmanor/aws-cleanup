"""Connection types for compute: EC2, launch templates, Auto Scaling, ECS, EKS,
Lambda, Step Functions, SageMaker. Each in all three states (connections_base)."""

import unittest

from .fakes import FakeSession, find_edge, make_row, audit, recent
from .connections_base import (
    ACCOUNT,
    ConnectionTypeTestCase,
    REGION,
    ec2_session,
    function_payload,
    instance_payload,
    lambda_session,
)


class EC2InstanceConnectionTests(ConnectionTypeTestCase):

    def test_ec2_ami_image_id(self):
        def build():
            rows = audit.collect_ec2_instances(ec2_session([instance_payload()]), REGION)
            return rows + [make_row("AMI", "ami-0123")]
        self.assert_three_states("ec2.ami.image-id", build, "i-0abc", "ami-0123")

    def test_ec2_keypair_key_name(self):
        def build():
            rows = audit.collect_ec2_instances(ec2_session([instance_payload()]), REGION)
            return rows + [make_row("KeyPair", "deploy-key")]
        self.assert_three_states("ec2.keypair.key-name", build, "i-0abc", "deploy-key")

    def test_ec2_securitygroup_membership(self):
        def build():
            rows = audit.collect_ec2_instances(ec2_session([instance_payload()]), REGION)
            return rows + [make_row("SecurityGroup", "sg-0123")]
        self.assert_three_states("ec2.securitygroup.membership", build, "i-0abc", "sg-0123")

    def test_keypair_edge_is_type_guarded_against_a_same_name_bucket(self):
        """A KeyName equal to an S3 bucket's name is a collision, never a merge."""
        with self._isolated("ec2.keypair.key-name", audit.CONN_ON):
            rows = audit.collect_ec2_instances(ec2_session([instance_payload()]), REGION)
            rows.append(make_row("S3Bucket", "deploy-key"))  # same name, unrelated
            links = audit.resolve_edges(rows)
            instance = self._row(rows, "i-0abc")
            self.assertIn("coincidental name collision", audit.render_connections(instance))
            self.assertEqual(links, [], "a type collision must not create a grouping link")


class LaunchTemplateConnectionTests(ConnectionTypeTestCase):

    @staticmethod
    def _session(data):
        return FakeSession({"ec2": {
            "describe_launch_templates": {"LaunchTemplates": [{
                "LaunchTemplateId": "lt-0abc", "LaunchTemplateName": "web-lt",
                "DefaultVersionNumber": 3, "LatestVersionNumber": 3, "CreateTime": recent(),
            }]},
            "describe_launch_template_versions": {
                "LaunchTemplateVersions": [{"LaunchTemplateData": data}]},
        }})

    def test_launchtemplate_ami_image_id(self):
        def build():
            rows = audit.collect_launch_templates(self._session({"ImageId": "ami-0123"}), REGION)
            return rows + [make_row("AMI", "ami-0123")]
        self.assert_three_states("launchtemplate.ami.image-id", build, "lt-0abc", "ami-0123")

    def test_launchtemplate_keypair_key_name(self):
        def build():
            rows = audit.collect_launch_templates(self._session({"KeyName": "deploy-key"}), REGION)
            return rows + [make_row("KeyPair", "deploy-key")]
        self.assert_three_states("launchtemplate.keypair.key-name", build, "lt-0abc", "deploy-key")

    def test_launchtemplate_securitygroup_membership(self):
        def build():
            rows = audit.collect_launch_templates(
                self._session({"SecurityGroupIds": ["sg-0123"]}), REGION)
            return rows + [make_row("SecurityGroup", "sg-0123")]
        self.assert_three_states(
            "launchtemplate.securitygroup.membership", build, "lt-0abc", "sg-0123")

    def test_security_groups_nested_under_network_interfaces_are_also_found(self):
        """A template can declare SGs on the template body OR inside its
        NetworkInterfaces block; missing the latter would under-report."""
        with self._isolated("launchtemplate.securitygroup.membership", audit.CONN_ON):
            rows = audit.collect_launch_templates(
                self._session({"NetworkInterfaces": [{"Groups": ["sg-nested"]}]}), REGION)
            self.assertIsNotNone(
                find_edge(rows[0], "launchtemplate.securitygroup.membership"))

    def test_spotrequest_ec2_instance_id(self):
        def build():
            session = FakeSession({"ec2": {"describe_spot_instance_requests": {
                "SpotInstanceRequests": [{
                    "SpotInstanceRequestId": "sir-0abc", "State": "active",
                    "CreateTime": recent(), "InstanceId": "i-0abc",
                    "Type": "one-time", "Status": {"Message": "fulfilled"},
                }]}}})
            rows = audit.collect_spot_instance_requests(session, REGION)
            return rows + [make_row("EC2Instance", "i-0abc")]
        self.assert_three_states("spotrequest.ec2.instance-id", build, "sir-0abc", "i-0abc")


class AutoScalingConnectionTests(ConnectionTypeTestCase):

    @staticmethod
    def _group():
        session = FakeSession({"autoscaling": {"describe_auto_scaling_groups": {
            "AutoScalingGroups": [{
                "AutoScalingGroupName": "workers", "DesiredCapacity": 1, "CreatedTime": recent(),
                "LaunchTemplate": {"LaunchTemplateId": "lt-0123"},
                "Instances": [{"InstanceId": "i-0abc"}], "VPCZoneIdentifier": "subnet-0123"}]}}})
        return audit.collect_auto_scaling_groups(session, REGION)

    def test_asg_launchtemplate_reference(self):
        self.assert_three_states("asg.launchtemplate.reference",
                                 lambda: self._group() + [make_row("LaunchTemplate", "lt-0123")],
                                 "workers", "lt-0123")

    def test_asg_ec2_instance_membership(self):
        self.assert_three_states("asg.ec2.instance-membership",
                                 lambda: self._group() + [make_row("EC2Instance", "i-0abc")],
                                 "workers", "i-0abc")

    def test_asg_targetgroup_attachment(self):
        def build():
            session = FakeSession({"autoscaling": {"describe_auto_scaling_groups": {
                "AutoScalingGroups": [{
                    "AutoScalingGroupName": "workers", "DesiredCapacity": 1, "CreatedTime": recent(),
                    "TargetGroupARNs": [
                        f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:targetgroup/blue/0123"]}]}}})
            return (audit.collect_auto_scaling_groups(session, REGION)
                    + [make_row("TargetGroup", "blue")])
        self.assert_three_states("asg.targetgroup.attachment", build, "workers", "blue")

    def test_targetgroup_loadbalancer_attachment(self):
        def build():
            session = FakeSession({"elbv2": {"describe_target_groups": {"TargetGroups": [{
                "TargetGroupName": "blue", "LoadBalancerArns": [
                    f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/app/web/abc"]}]}}})
            return audit.collect_target_groups(session, REGION) + [make_row("LoadBalancer", "web")]
        self.assert_three_states("targetgroup.loadbalancer.attachment", build, "blue", "web")

    def test_asg_subnet_placement(self):
        self.assert_three_states("asg.subnet.placement",
                                 lambda: self._group() + [make_row("Subnet", "subnet-0123")],
                                 "workers", "subnet-0123")


class EcsConnectionTests(ConnectionTypeTestCase):

    @staticmethod
    def _ecs(**service):
        svc = {"serviceName": "api", "serviceArn": f"arn:aws:ecs:{REGION}:{ACCOUNT}:service/main/api",
               "desiredCount": 1, "runningCount": 1, "createdAt": recent(), "taskDefinition": "api:1"}
        svc.update(service)
        task = {"taskRoleArn": f"arn:aws:iam::{ACCOUNT}:role/api-task", "containerDefinitions": [
            {"image": f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/api-images:v3"}]}
        lb_arn = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/app/web/abc"
        session = FakeSession({
            "ecs": {
                "list_clusters": {"clusterArns": [f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/main"]},
                "describe_clusters": {"clusters": [{
                    "clusterName": "main", "clusterArn": f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/main"}]},
                "list_services": {"serviceArns": [svc["serviceArn"]]},
                "describe_services": {"services": [svc]},
                "describe_task_definition": {"taskDefinition": task},
            },
            "elbv2": {"describe_target_groups": {"TargetGroups": [{"LoadBalancerArns": [lb_arn]}]}},
        })
        return audit.collect_ecs(session, REGION)

    _TG = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:targetgroup/api-tg/0123"

    def test_ecscluster_ec2instance_container_instance(self):
        def build():
            cluster = f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/main"
            session = FakeSession({"ecs": {
                "list_clusters": {"clusterArns": [cluster]},
                "describe_clusters": {"clusters": [{"clusterName": "main", "clusterArn": cluster,
                                                    "registeredContainerInstancesCount": 1}]},
                "list_container_instances": {"containerInstanceArns": ["arn:ci/1"]},
                "describe_container_instances": {"containerInstances": [{"ec2InstanceId": "i-0node"}]},
                "list_services": {"serviceArns": []},
            }, "elbv2": {}})
            return audit.collect_ecs(session, REGION) + [make_row("EC2Instance", "i-0node")]
        self.assert_three_states("ecscluster.ec2instance.container-instance", build, "main", "i-0node")

    def test_ecsservice_ecscluster_membership(self):
        self.assert_three_states("ecsservice.ecscluster.membership",
                                 lambda: [r for r in self._ecs() if r["service"] == "ECSService"]
                                 + [make_row("ECSCluster", "main")], "main/api", "main")

    def test_ecsservice_subnet_placement(self):
        net = {"awsvpcConfiguration": {"subnets": ["subnet-0123"], "securityGroups": []}}
        self.assert_three_states("ecsservice.subnet.placement",
                                 lambda: self._ecs(networkConfiguration=net)
                                 + [make_row("Subnet", "subnet-0123")], "main/api", "subnet-0123")

    def test_ecsservice_securitygroup_membership(self):
        net = {"awsvpcConfiguration": {"subnets": [], "securityGroups": ["sg-0123"]}}
        self.assert_three_states("ecsservice.securitygroup.membership",
                                 lambda: self._ecs(networkConfiguration=net)
                                 + [make_row("SecurityGroup", "sg-0123")], "main/api", "sg-0123")

    def test_ecsservice_targetgroup_attachment(self):
        self.assert_three_states("ecsservice.targetgroup.attachment",
                                 lambda: self._ecs(loadBalancers=[{"targetGroupArn": self._TG}])
                                 + [make_row("TargetGroup", "api-tg")], "main/api", "api-tg")

    def test_ecsservice_loadbalancer_target(self):
        self.assert_three_states("ecsservice.loadbalancer.target",
                                 lambda: self._ecs(loadBalancers=[{"targetGroupArn": self._TG}])
                                 + [make_row("LoadBalancer", "web")], "main/api", "web")

    def test_ecsservice_iamrole_task_role(self):
        self.assert_three_states("ecsservice.iamrole.task-role",
                                 lambda: self._ecs() + [make_row("IAMRole", "api-task", region="global")],
                                 "main/api", "api-task")

    def test_ecsservice_ecrrepository_image(self):
        self.assert_three_states("ecsservice.ecrrepository.image",
                                 lambda: self._ecs() + [make_row("ECRRepository", "api-images")],
                                 "main/api", "api-images")


class EksConnectionTests(ConnectionTypeTestCase):

    @staticmethod
    def _cluster(**cluster):
        session = FakeSession({"eks": {
            "list_clusters": {"clusters": ["platform"]},
            "describe_cluster": {"cluster": dict({"name": "platform", "createdAt": recent()}, **cluster)},
            "list_nodegroups": {"nodegroups": ["workers"]},
            "describe_nodegroup": {"nodegroup": {
                "scalingConfig": {"desiredSize": 2},
                "resources": {"autoScalingGroups": [{"name": "eks-workers-asg"}]}}},
            "list_fargate_profiles": {"fargateProfileNames": []},
        }})
        return audit.collect_eks_clusters(session, REGION)

    def test_ekscluster_subnet_placement(self):
        self.assert_three_states(
            "ekscluster.subnet.placement",
            lambda: self._cluster(resourcesVpcConfig={"subnetIds": ["subnet-0123"]})
            + [make_row("Subnet", "subnet-0123")], "platform", "subnet-0123")

    def test_ekscluster_securitygroup_membership(self):
        self.assert_three_states(
            "ekscluster.securitygroup.membership",
            lambda: self._cluster(resourcesVpcConfig={"securityGroupIds": ["sg-0123"]})
            + [make_row("SecurityGroup", "sg-0123")], "platform", "sg-0123")

    def test_ekscluster_iamrole_cluster_role(self):
        self.assert_three_states(
            "ekscluster.iamrole.cluster-role",
            lambda: self._cluster(roleArn=f"arn:aws:iam::{ACCOUNT}:role/eks-cluster")
            + [make_row("IAMRole", "eks-cluster", region="global")], "platform", "eks-cluster")

    def test_ekscluster_autoscalinggroup_nodegroup(self):
        self.assert_three_states(
            "ekscluster.autoscalinggroup.nodegroup",
            lambda: self._cluster() + [make_row("AutoScalingGroup", "eks-workers-asg")],
            "platform", "eks-workers-asg")


class LambdaConnectionTests(ConnectionTypeTestCase):

    def test_lambda_ecrrepository_image(self):
        image = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/fn-images:v2"
        self.assert_three_states(
            "lambda.ecrrepository.image",
            lambda: audit.collect_lambda_functions(lambda_session(
                [function_payload(PackageType="Image")],
                extra={"get_function": {"Code": {"ImageUri": image}}}), REGION)
            + [make_row("ECRRepository", "fn-images")], "orders-fn", "fn-images")

    def test_lambda_env_var_arn_value(self):
        def build():
            fn = function_payload(Environment={"Variables": {
                "TABLE": f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/orders"}})
            rows = audit.collect_lambda_functions(lambda_session([fn]), REGION)
            return rows + [make_row("DynamoDBTable", "orders")]
        self.assert_three_states("lambda.any.env-var-arn-value", build, "orders-fn", "orders")

    def test_lambda_env_var_arn_embedded(self):
        def build():
            fn = function_payload(Environment={"Variables": {
                "CONFIG": f'{{"table":"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/orders"}}'}})
            rows = audit.collect_lambda_functions(lambda_session([fn]), REGION)
            return rows + [make_row("DynamoDBTable", "orders")]
        self.assert_three_states("lambda.any.env-var-arn-embedded", build, "orders-fn", "orders")

    def test_lambda_env_var_execute_api_url(self):
        def build():
            fn = function_payload(Environment={"Variables": {
                "API": f"https://abc123.execute-api.{REGION}.amazonaws.com/prod"}})
            rows = audit.collect_lambda_functions(lambda_session([fn]), REGION)
            return rows + [make_row("APIGatewayRestApi", "abc123")]
        self.assert_three_states(
            "lambda.apigateway.env-var-execute-api-url", build, "orders-fn", "abc123")

    def test_lambda_env_var_bare_name(self):
        def build():
            fn = function_payload(Environment={"Variables": {"TABLE_NAME": "orders"}})
            rows = audit.collect_lambda_functions(lambda_session([fn]), REGION)
            return rows + [make_row("DynamoDBTable", "orders")]
        self.assert_three_states("lambda.any.env-var-bare-name", build, "orders-fn", "orders")

    def test_bare_name_strategy_is_report_only_by_default(self):
        """Its whole reason for existing is that it is too weak to cluster on,
        so the shipped default must not group with it."""
        self.assertEqual(
            audit.CONNECTION_TYPES_BY_ID["lambda.any.env-var-bare-name"].default_state,
            audit.CONN_REPORT_ONLY)

    def test_lambda_loggroup_logging_config(self):
        def build():
            fn = function_payload(LoggingConfig={"LogGroup": "/custom/orders"})
            rows = audit.collect_lambda_functions(lambda_session([fn]), REGION)
            return rows + [make_row("CloudWatchLogGroup", "/custom/orders")]
        self.assert_three_states(
            "lambda.loggroup.logging-config", build, "orders-fn", "/custom/orders")

    def test_alias_pinned_versions_contribute_their_own_env_vars(self):
        """An aliased published version has its own env vars, which are read too."""
        with self._isolated("lambda.any.env-var-arn-value", audit.CONN_ON):
            session = lambda_session(
                [function_payload(Environment={"Variables": {
                    "TABLE": f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/latest-table"}})],
                aliases=[{"Name": "prod", "FunctionVersion": "3"}],
                extra={"get_function_configuration": {"Environment": {"Variables": {
                    "TABLE": f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/prod-table"}}}},
            )
            rows = audit.collect_lambda_functions(session, REGION)
            targets = {e["target_id"] for e in rows[0]["_edges"]}
            self.assertEqual(targets, {"latest-table", "prod-table"})

    def test_lambda_dead_letter_config_to_sqs(self):
        """A Lambda DLQ (SQS or SNS) resolves now that both are scanned."""
        def build():
            fn = function_payload(DeadLetterConfig={
                "TargetArn": f"arn:aws:sqs:{REGION}:{ACCOUNT}:orders-dlq"})
            rows = audit.collect_lambda_functions(lambda_session([fn]), REGION)
            return rows + [make_row("SQSQueue", "orders-dlq")]
        self.assert_three_states(
            "lambda.any.dead-letter-config", build, "orders-fn", "orders-dlq")

    def test_lambda_dead_letter_config_to_sns(self):
        def build():
            fn = function_payload(DeadLetterConfig={
                "TargetArn": f"arn:aws:sns:{REGION}:{ACCOUNT}:orders-alerts"})
            rows = audit.collect_lambda_functions(lambda_session([fn]), REGION)
            return rows + [make_row("SNSTopic", "orders-alerts")]
        self.assert_three_states(
            "lambda.any.dead-letter-config", build, "orders-fn", "orders-alerts")


class EventSourceMappingTests(ConnectionTypeTestCase):
    """What invokes a function, from the mappings AWS holds."""

    def _build(self, state="Enabled", source=None):
        source = source or f"arn:aws:sqs:{REGION}:{ACCOUNT}:orders-queue"

        def build():
            session = lambda_session([function_payload()])
            session.get("lambda").responses["list_event_source_mappings"] = {
                "EventSourceMappings": [{
                    "UUID": "map-1", "State": state,
                    "EventSourceArn": source,
                    "FunctionArn": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:orders-fn",
                }]}
            rows = audit.collect_lambda_functions(session, REGION)
            return rows + [make_row("SQSQueue", "orders-queue")]
        return build

    def test_lambda_any_event_source_mapping(self):
        self.assert_three_states("lambda.any.event-source-mapping", self._build(),
                                 "orders-fn", "orders-queue")

    def test_a_disabled_mapping_is_still_a_configured_relationship(self):
        """A disabled mapping is still recorded: it is evidence about the deployment."""
        rows = self._build(state="Disabled")()
        audit.resolve_edges(rows)
        queue = self._row(rows, "orders-queue")

        self.assertIn("consumes events from",
                      audit.render_connections(self._row(rows, "orders-fn")))
        self.assertIn("Disabled", audit.render_connections(queue))

    def test_a_dynamodb_stream_resolves_to_its_table(self):
        stream = (f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/orders"
                  "/stream/2026-01-01T00:00:00.000")
        rows = self._build(source=stream)()
        rows = [r for r in rows if r["service"] != "SQSQueue"]
        rows.append(make_row("DynamoDBTable", "orders"))
        audit.resolve_edges(rows)

        self.assertIn("DynamoDBTable:orders",
                      audit.render_connections(self._row(rows, "orders-fn")))

    def test_an_unsupported_source_stays_unresolved(self):
        """A Kinesis stream is a real source this script does not inventory.
        It must not become an invented row."""
        kinesis = f"arn:aws:kinesis:{REGION}:{ACCOUNT}:stream/clicks"
        rows = self._build(source=kinesis)()
        before = len(rows)
        audit.resolve_edges(rows)
        self.assertEqual(len(rows), before)


class SageMakerConnectionTests(ConnectionTypeTestCase):

    @staticmethod
    def _notebook(**detail):
        session = FakeSession({"sagemaker": {
            "list_notebook_instances": {"NotebookInstances": [{
                "NotebookInstanceName": "scratch", "CreationTime": recent()}]},
            "describe_notebook_instance": detail,
        }})
        return audit.collect_sagemaker_notebooks(session, REGION)

    def test_sagemakernotebook_subnet_placement(self):
        self.assert_three_states("sagemakernotebook.subnet.placement",
                                 lambda: self._notebook(SubnetId="subnet-0123")
                                 + [make_row("Subnet", "subnet-0123")], "scratch", "subnet-0123")

    def test_sagemakernotebook_iamrole_role(self):
        self.assert_three_states("sagemakernotebook.iamrole.role",
                                 lambda: self._notebook(RoleArn=f"arn:aws:iam::{ACCOUNT}:role/nb-role")
                                 + [make_row("IAMRole", "nb-role", region="global")],
                                 "scratch", "nb-role")


if __name__ == "__main__":
    unittest.main()
