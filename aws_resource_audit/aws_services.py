"""Which AWS product each resource type belongs to (by product-URL slug) and its
short name. Shared by the coverage CSV and the overview's By service view."""

# Judgement calls: EBS is its own product (Storage), not EC2; CloudWatch Logs has no
# product page, so log groups share "cloudwatch". Groupings follow AWS's taxonomy.
SERVICE_SLUGS = {
    # Compute - the ec2: API surface minus the parts AWS sells separately
    "EC2Instance":                "ec2",
    "AMI":                        "ec2",
    "KeyPair":                    "ec2",
    "SecurityGroup":              "ec2",
    "NetworkInterface":           "ec2",
    "VPC":                        "vpc",
    "Subnet":                     "vpc",
    "RouteTable":                 "vpc",
    "InternetGateway":            "vpc",
    "NatGateway":                 "vpc",
    "VpcEndpoint":                "vpc",
    "ElasticIP":                  "ec2",
    "LaunchTemplate":             "ec2",
    "SpotInstanceRequest":        "ec2",
    "ReservedInstance":           "ec2",
    "LambdaFunction":             "lambda",
    "ElasticBeanstalkEnvironment": "elasticbeanstalk",
    "ElasticBeanstalkApplication": "elasticbeanstalk",

    # Storage
    "EBSVolume":                  "ebs",
    "EBSSnapshot":                "ebs",
    "S3Bucket":                   "s3",
    "EFSFileSystem":              "efs",

    # Databases
    "RDSInstance":                "rds",
    # AWS files Aurora as its own product; a Multi-AZ DB cluster lands here too.
    "RDSCluster":                 "rds/aurora",
    "DocumentDBCluster":          "documentdb",
    "DocumentDBInstance":         "documentdb",
    "NeptuneCluster":             "neptune",
    "NeptuneInstance":            "neptune",
    "RDSSnapshot":                "rds",
    "DynamoDBTable":              "dynamodb",
    "ElastiCacheCluster":         "elasticache",
    "OpenSearchDomain":           "opensearch-service",
    "OpenSearchServerlessCollection": "opensearch-service",

    # Networking & content delivery
    "LoadBalancer":               "elasticloadbalancing",

    # Application integration
    "SQSQueue":                   "sqs",
    "KinesisStream":              "kinesis",
    "FirehoseStream":             "kinesis",
    "MSKCluster":                 "msk",
    "SNSTopic":                   "sns",
    "EventBridgeRule":            "eventbridge",
    "EventBridgeSchedule":        "eventbridge",
    "EventBridgeBus":             "eventbridge",

    # Front-end web & mobile - AWS files API Gateway here, not under
    # Application Integration where its role in this tool would suggest
    "APIGatewayRestApi":          "api-gateway",
    "APIGatewayV2Api":            "api-gateway",
    "APIGatewayDomainName":       "api-gateway",
    "AppSyncApi":                 "appsync",
    "AmplifyApp":                 "amplify",

    # Security, identity & compliance
    "CognitoUserPool":            "cognito",
    "KMSKey":                     "kms",
    "ACMCertificate":             "certificate-manager",
    "SSMParameter":               "systems-manager",
    "Secret":                     "secrets-manager",
    "Route53HostedZone":          "route53",
    "Route53ResolverEndpoint":    "route53",
    "WAFWebACL":                  "waf",
    "NetworkFirewall":            "network-firewall",
    "TransitGateway":             "transit-gateway",
    "VPNConnection":              "vpn",
    "CloudFrontDistribution":     "cloudfront",
    "ECRRepository":              "ecr",
    "AutoScalingGroup":           "ec2/autoscaling",
    "TargetGroup":                "elasticloadbalancing",
    "StepFunctionsStateMachine":  "step-functions",
    "ECSCluster":                 "ecs",
    "ECSService":                 "ecs",
    "CodeBuildProject":           "codebuild",
    "CodePipeline":               "codepipeline",
    "GlueJob":                    "glue",
    "GlueCrawler":                "glue",
    "BackupVault":                "backup",
    "BackupPlan":                 "backup",
    "SageMakerEndpoint":          "sagemaker",
    "SageMakerNotebook":          "sagemaker",
    "SageMakerStudioApp":         "sagemaker",
    "SageMakerTrainingJob":       "sagemaker",
    "EKSCluster":                 "eks",
    "RedshiftCluster":            "redshift",
    "RedshiftServerlessWorkgroup": "redshift",
    "IAMUser":                    "iam",
    "IAMRole":                    "iam",
    "IAMGroup":                   "iam",
    "IAMPolicy":                  "iam",
    "IAMRoleUnusedAccessFinding": "iam",

    # Management & governance
    "CloudWatchLogGroup":         "cloudwatch",
    "CloudWatchAlarm":            "cloudwatch",
    "CloudTrailTrail":            "cloudtrail",
    "CloudFormationStack":        "cloudformation",

    # Developer tools - AWS files X-Ray here rather than with the other
    # observability services, which is why tracing sits apart from CloudWatch
    "XRayGroup":                  "xray",
    "XRaySamplingRule":           "xray",
}


# The short name a bubble can fit. AWS's own names ("Amazon Simple Storage
# Service (S3)") are in the coverage CSV; these are the forms people say.
SERVICE_LABELS = {
    "ec2": "EC2",
    "lambda": "Lambda",
    "elasticbeanstalk": "Elastic Beanstalk",
    "ebs": "EBS",
    "s3": "S3",
    "efs": "EFS",
    "elasticache": "ElastiCache",
    "opensearch-service": "OpenSearch",
    "rds": "RDS",
    "rds/aurora": "Aurora",
    "documentdb": "DocumentDB",
    "neptune": "Neptune",
    "dynamodb": "DynamoDB",
    "elasticloadbalancing": "ELB",
    "sqs": "SQS",
    "kinesis": "Kinesis",
    "msk": "MSK",
    "sns": "SNS",
    "eventbridge": "EventBridge",
    "api-gateway": "API Gateway",
    "appsync": "AppSync",
    "amplify": "Amplify",
    "cognito": "Cognito",
    "kms": "KMS",
    "secrets-manager": "Secrets Manager",
    "certificate-manager": "Certificate Manager",
    "systems-manager": "Systems Manager",
    "route53": "Route 53",
    "waf": "WAF",
    "network-firewall": "Network Firewall",
    "cloudfront": "CloudFront",
    "ecr": "ECR",
    "ec2/autoscaling": "EC2 Auto Scaling",
    "step-functions": "Step Functions",
    "ecs": "ECS",
    "codebuild": "CodeBuild",
    "codepipeline": "CodePipeline",
    "glue": "Glue",
    "backup": "Backup",
    "sagemaker": "SageMaker",
    "eks": "EKS",
    "redshift": "Redshift",
    "iam": "IAM",
    "cloudwatch": "CloudWatch",
    "cloudtrail": "CloudTrail",
    "cloudformation": "CloudFormation",
    "xray": "X-Ray",
    "vpc": "VPC",
    "transit-gateway": "Transit Gateway",
    "vpn": "Site-to-Site VPN",
}


def types_by_slug():
    """slug -> sorted list of the internal resource types filed under it."""
    out = {}
    for resource_type, slug in SERVICE_SLUGS.items():
        out.setdefault(slug, []).append(resource_type)
    return {slug: sorted(types) for slug, types in out.items()}


def service_of(resource_type):
    """(slug, label) for a resource type; the type itself when it has no slug."""
    slug = SERVICE_SLUGS.get(resource_type)
    if not slug:
        return resource_type, resource_type
    return slug, SERVICE_LABELS.get(slug, slug)
