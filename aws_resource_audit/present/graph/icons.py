"""Which AWS icon each architecture node is drawn with. The artwork is embedded
(aws_icons_data.py, Iconify logos, CC0), so no network is needed."""

from .aws_icons_data import ICON_PACK

# The Mermaid icon-pack prefix: a node is drawn with icon "aws:<name>".
ICON_PREFIX = ICON_PACK["prefix"]

# Resource type -> icon name in ICON_PACK. A type missing here draws the
# generic "aws" icon; tests fail if a type the architecture draws is missing.
SERVICE_ICONS = {
    "EC2Instance": "ec2",
    "LambdaFunction": "lambda",
    "ElasticBeanstalkEnvironment": "elastic-beanstalk",
    "S3Bucket": "s3",
    "RDSInstance": "rds",
    "RDSCluster": "aurora",
    "DocumentDBCluster": "documentdb",
    "DocumentDBInstance": "documentdb",
    "NeptuneCluster": "neptune",
    "NeptuneInstance": "neptune",
    # The CC0 logos set has no EFS mark; the generic AWS icon stands in.
    "EFSFileSystem": "aws",
    "ElastiCacheCluster": "elasticache",
    "OpenSearchDomain": "open-search",
    "OpenSearchServerlessCollection": "open-search",
    "CloudFrontDistribution": "cloudfront",
    "Route53HostedZone": "route53",
    "Route53ResolverEndpoint": "route53",
    "WAFWebACL": "waf",
    # The CC0 logos set has no Network Firewall mark; the generic AWS icon stands in.
    "NetworkFirewall": "aws",
    # Nor a Transit Gateway or VPN mark.
    "TransitGateway": "aws",
    "VPNConnection": "aws",
    "StepFunctionsStateMachine": "step-functions",
    "ECSService": "ecs",
    # The CC0 logos set has no SageMaker mark; the generic AWS icon stands in.
    "SageMakerEndpoint": "aws",
    "EKSCluster": "eks",
    "RedshiftCluster": "redshift",
    "RedshiftServerlessWorkgroup": "redshift",
    "DynamoDBTable": "dynamodb",
    "AmplifyApp": "amplify",
    "APIGatewayRestApi": "api-gateway",
    "APIGatewayV2Api": "api-gateway",
    "AppSyncApi": "appsync",
    "SQSQueue": "sqs",
    "KinesisStream": "kinesis",
    "FirehoseStream": "kinesis",
    "MSKCluster": "msk",
    "SNSTopic": "sns",
    "EventBridgeRule": "eventbridge",
    "EventBridgeSchedule": "eventbridge",
    "EventBridgeBus": "eventbridge",
    "LoadBalancer": "elb",
    "CognitoUserPool": "cognito",
}

# Shipped ahead of use, so adding a type is a line
# in SERVICE_ICONS rather than a regeneration.
EXTRA_ICONS = (
    "aws", "vpc", "cloudfront", "route53", "waf", "shield", "elasticache",
    "open-search", "step-functions", "ecs", "fargate", "eks", "kinesis",
    "aurora", "secrets-manager", "kms", "mq", "msk", "appsync",
)


def icon_for(service):
    """The full Mermaid icon reference for a resource type."""
    return f"{ICON_PREFIX}:{SERVICE_ICONS.get(service, 'aws')}"
