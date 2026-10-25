"""AWS Console deep link per resource type: the report's and the web app's
clickable resource IDs. Every scanned type has an entry; a test enforces it."""

import urllib.parse

_EC2 = "https://{region}.console.aws.amazon.com/ec2/home?region={region}#"
_VPC = "https://{region}.console.aws.amazon.com/vpcconsole/home?region={region}#"
_RDS = "https://{region}.console.aws.amazon.com/rds/home?region={region}#"
_IAM = "https://us-east-1.console.aws.amazon.com/iam/home#/"
_SAGEMAKER = "https://{region}.console.aws.amazon.com/sagemaker/home?region={region}#/"
_CLOUDWATCH = "https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#"

# Placeholders: region, account, id (resource_id), arn, name, plus _EXTRA_FIELDS.
# Where no detail page can be addressed from the row, a (filtered) list page.
CONSOLE_URLS = {
    "EC2Instance": _EC2 + "InstanceDetails:instanceId={id}",
    "EBSVolume": _EC2 + "VolumeDetails:volumeId={id}",
    "EBSSnapshot": _EC2 + "SnapshotDetails:snapshotId={id}",
    "AMI": _EC2 + "ImageDetails:imageId={id}",
    "ElasticIP": _EC2 + "Addresses:",
    "LaunchTemplate": _EC2 + "LaunchTemplateDetails:launchTemplateId={id}",
    "SecurityGroup": _EC2 + "SecurityGroup:groupId={id}",
    "NetworkInterface": _EC2 + "NetworkInterface:networkInterfaceId={id}",
    "KeyPair": _EC2 + "KeyPairs:search={id}",
    "ReservedInstance": _EC2 + "ReservedInstances:search={id}",
    "SpotInstanceRequest": _EC2 + "SpotInstances:search={id}",
    "LoadBalancer": _EC2 + "LoadBalancers:search={id}",
    "TargetGroup": _EC2 + "TargetGroup:targetGroupArn={arn}",
    "AutoScalingGroup": _EC2 + "AutoScalingGroupDetails:id={id};view=details",
    "VPC": _VPC + "VpcDetails:VpcId={id}",
    "Subnet": _VPC + "SubnetDetails:subnetId={id}",
    "RouteTable": _VPC + "RouteTableDetails:RouteTableId={id}",
    "InternetGateway": _VPC + "InternetGateway:internetGatewayId={id}",
    "NatGateway": _VPC + "NatGatewayDetails:natGatewayId={id}",
    "VpcEndpoint": _VPC + "EndpointDetails:vpcEndpointId={id}",
    "TransitGateway": _VPC + "TransitGatewayDetails:transitGatewayId={id}",
    "VPNConnection": _VPC + "VpnConnectionDetails:VpnConnectionId={id}",
    "NetworkFirewall": _VPC + "NetworkFirewalls:",
    "RDSInstance": _RDS + "database:id={id};is-cluster=false",
    "RDSCluster": _RDS + "database:id={id};is-cluster=true",
    "RDSSnapshot": _RDS + "{snapshot_kind}:id={id}",
    "DocumentDBCluster": "https://{region}.console.aws.amazon.com/docdb/home?region={region}#cluster-details/{id}",
    "DocumentDBInstance": "https://{region}.console.aws.amazon.com/docdb/home?region={region}#instance-details/{id}",
    "NeptuneCluster": "https://{region}.console.aws.amazon.com/neptune/home?region={region}#database:id={id};is-cluster=true",
    "NeptuneInstance": "https://{region}.console.aws.amazon.com/neptune/home?region={region}#database:id={id};is-cluster=false",
    "DynamoDBTable": "https://{region}.console.aws.amazon.com/dynamodbv2/home?region={region}#table?name={id}",
    "ElastiCacheCluster": "https://{region}.console.aws.amazon.com/elasticache/home?region={region}",
    "OpenSearchDomain": "https://{region}.console.aws.amazon.com/aos/home?region={region}#opensearch/domains/{id}",
    "OpenSearchServerlessCollection": "https://{region}.console.aws.amazon.com/aos/home?region={region}#opensearch/collections/{name}",
    "RedshiftCluster": "https://{region}.console.aws.amazon.com/redshiftv2/home?region={region}#cluster-details?cluster={id}",
    "RedshiftServerlessWorkgroup": "https://{region}.console.aws.amazon.com/redshiftv2/home?region={region}#serverless-workgroup?workgroup={id}",
    "S3Bucket": "https://s3.console.aws.amazon.com/s3/buckets/{id}",
    "EFSFileSystem": "https://{region}.console.aws.amazon.com/efs/home?region={region}#/file-systems/{id}",
    "BackupVault": "https://{region}.console.aws.amazon.com/backup/home?region={region}#/backupvaults/details/{id}",
    "BackupPlan": "https://{region}.console.aws.amazon.com/backup/home?region={region}#/backupplan/details/{id}",
    "LambdaFunction": "https://{region}.console.aws.amazon.com/lambda/home?region={region}#/functions/{id}",
    "ECRRepository": "https://{region}.console.aws.amazon.com/ecr/repositories/private/{account}/{id_path}?region={region}",
    "ECSCluster": "https://{region}.console.aws.amazon.com/ecs/v2/clusters/{id}/services?region={region}",
    "ECSService": "https://{region}.console.aws.amazon.com/ecs/v2/clusters/{cluster}/services/{service}/health?region={region}",
    "EKSCluster": "https://{region}.console.aws.amazon.com/eks/home?region={region}#/clusters/{id}",
    "ElasticBeanstalkEnvironment": "https://{region}.console.aws.amazon.com/elasticbeanstalk/home?region={region}#/environment/dashboard?environmentId={id}",
    "ElasticBeanstalkApplication": "https://{region}.console.aws.amazon.com/elasticbeanstalk/home?region={region}#/application/overview?applicationName={id}",
    "AmplifyApp": "https://{region}.console.aws.amazon.com/amplify/home?region={region}#/{id}",
    "CloudFormationStack": "https://{region}.console.aws.amazon.com/cloudformation/home?region={region}#/stacks/stackinfo?stackId={id}",
    "CodeBuildProject": "https://{region}.console.aws.amazon.com/codesuite/codebuild/projects/{id}?region={region}",
    "CodePipeline": "https://{region}.console.aws.amazon.com/codesuite/codepipeline/pipelines/{id}/view?region={region}",
    "StepFunctionsStateMachine": "https://{region}.console.aws.amazon.com/states/home?region={region}#/statemachines/view/{arn}",
    "GlueJob": "https://{region}.console.aws.amazon.com/gluestudio/home?region={region}#/editor/job/{id}/details",
    "GlueCrawler": "https://{region}.console.aws.amazon.com/glue/home?region={region}#/v2/data-catalog/crawlers/view/{id}",
    "APIGatewayRestApi": "https://{region}.console.aws.amazon.com/apigateway/main/apis/{id}/resources?region={region}",
    "APIGatewayV2Api": "https://{region}.console.aws.amazon.com/apigateway/main/apis/{id}/routes?region={region}",
    "APIGatewayDomainName": "https://{region}.console.aws.amazon.com/apigateway/main/publish/domain-names?domain={id}&region={region}",
    "AppSyncApi": "https://{region}.console.aws.amazon.com/appsync/home?region={region}#/{id}/v1/home",
    "CloudFrontDistribution": "https://us-east-1.console.aws.amazon.com/cloudfront/v4/home#/distributions/{id}",
    "Route53HostedZone": "https://us-east-1.console.aws.amazon.com/route53/v2/hostedzones#ListRecordSets/{id}",
    "Route53ResolverEndpoint": "https://{region}.console.aws.amazon.com/route53resolver/home?region={region}#/endpoints",
    "WAFWebACL": "https://us-east-1.console.aws.amazon.com/wafv2/homev2/web-acl/{id}/{acl_id}/overview?region={region}",
    "ACMCertificate": "https://{region}.console.aws.amazon.com/acm/home?region={region}#/certificates/{id}",
    "SQSQueue": "https://{region}.console.aws.amazon.com/sqs/v3/home?region={region}#/queues/{queue_url}",
    "SNSTopic": "https://{region}.console.aws.amazon.com/sns/v3/home?region={region}#/topic/{arn}",
    "EventBridgeBus": "https://{region}.console.aws.amazon.com/events/home?region={region}#/eventbus/{id}",
    "EventBridgeRule": "https://{region}.console.aws.amazon.com/events/home?region={region}#/eventbus/{bus}/rules/{id}",
    "EventBridgeSchedule": "https://{region}.console.aws.amazon.com/scheduler/home?region={region}#schedules/{group}/{id}",
    "KinesisStream": "https://{region}.console.aws.amazon.com/kinesis/home?region={region}#/streams/details/{id}/monitoring",
    "FirehoseStream": "https://{region}.console.aws.amazon.com/firehose/home?region={region}#/details/{id}/monitoring",
    "MSKCluster": "https://{region}.console.aws.amazon.com/msk/home?region={region}#/cluster/{arn}/view",
    "KMSKey": "https://{region}.console.aws.amazon.com/kms/home?region={region}#/kms/keys/{id}",
    "Secret": "https://{region}.console.aws.amazon.com/secretsmanager/secret?name={id}&region={region}",
    "SSMParameter": "https://{region}.console.aws.amazon.com/systems-manager/parameters/{id}/description?region={region}",
    "CognitoUserPool": "https://{region}.console.aws.amazon.com/cognito/v2/idp/user-pools/{id}/users?region={region}",
    "SageMakerEndpoint": _SAGEMAKER + "endpoints/{id}",
    "SageMakerNotebook": _SAGEMAKER + "notebook-instances/{id}",
    "SageMakerTrainingJob": _SAGEMAKER + "jobs/{id}",
    "SageMakerStudioApp": _SAGEMAKER + "studio/{domain}",
    "CloudWatchLogGroup": _CLOUDWATCH + "logsV2:log-groups/log-group/{id}",
    "CloudWatchAlarm": _CLOUDWATCH + "alarmsV2:",
    "XRayGroup": _CLOUDWATCH + "xray:settings/groups",
    "XRaySamplingRule": _CLOUDWATCH + "xray:settings/sampling-rules",
    "CloudTrailTrail": "https://{region}.console.aws.amazon.com/cloudtrail/home?region={region}#/configuration",
    "IAMRole": _IAM + "roles/details/{id}",
    "IAMUser": _IAM + "users/details/{id}",
    "IAMGroup": _IAM + "groups/details/{id}",
    # resource_id is the policy's ARN, which is what the policy detail page keys on.
    "IAMPolicy": _IAM + "policies/details/{id}?section=permissions",
    "IAMRoleUnusedAccessFinding": _IAM + "roles/details/{role}",
}


def _arn_resource_parts(row, prefix):
    """The "/"-separated parts after `prefix` in the row's ARN, or []."""
    resource = (row.get("arn") or "").split(":", 5)[-1]
    return resource[len(prefix):].split("/") if resource.startswith(prefix) else []


def _rule_bus(row):
    parts = _arn_resource_parts(row, "rule/")     # rule/<name> or rule/<bus>/<name>
    return parts[0] if len(parts) > 1 else "default"


def _schedule_group(row):
    parts = _arn_resource_parts(row, "schedule/")  # schedule/<group>/<name>
    return parts[0] if len(parts) > 1 else "default"


# Values a template needs beyond the row's own fields, raw; they are URL-quoted later.
_EXTRA_FIELDS = {
    "ECSService": lambda row: dict(zip(("cluster", "service"),     # <cluster>/<service>
                                       (row["resource_id"].split("/", 1) + [""])[:2])),
    "EventBridgeRule": lambda row: {"bus": _rule_bus(row)},
    "EventBridgeSchedule": lambda row: {"group": _schedule_group(row)},
    "WAFWebACL": lambda row: {"acl_id": (row.get("arn") or "").rsplit("/", 1)[-1]},
    "SageMakerStudioApp": lambda row: {"domain": row["resource_id"].split("/", 1)[0]},
    "RDSSnapshot": lambda row: {"snapshot_kind": "db-cluster-snapshot"
                                if ":cluster-snapshot:" in (row.get("arn") or "")
                                else "db-snapshot"},
    "IAMRoleUnusedAccessFinding": lambda row: {"role": row["resource_id"].rsplit("/", 1)[-1]},
    "SQSQueue": lambda row: {"queue_url": f"https://sqs.{row.get('region', '')}.amazonaws.com/"
                                          f"{row.get('account', '')}/{row['resource_id']}"},
}


def build_console_url(row):
    """AWS Console deep link for a resource, or "" for an unknown type or a
    failed-lookup row (which names no resource)."""
    template = CONSOLE_URLS.get(row["service"])
    if template is None or row.get("flag") == "ERROR":
        return ""

    def q(value):
        return urllib.parse.quote(str(value or ""), safe="")

    rid = row["resource_id"]
    fields = {"region": row.get("region", ""), "account": row.get("account", ""),
              "id": q(rid), "id_path": urllib.parse.quote(rid, safe="/"),
              "arn": q(row.get("arn")), "name": q(row.get("name"))}
    extra = _EXTRA_FIELDS.get(row["service"])
    if extra:
        fields.update({k: q(v) for k, v in extra(row).items()})
    return template.format(**fields)
