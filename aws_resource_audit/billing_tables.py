"""How the AWS bill maps onto this scan's resource types: Cost Explorer names,
what each bill line covers, and how usage types read. Split out of config.py."""

from decimal import Decimal

# Internal service -> Cost Explorer SERVICE names it rolls up to. Several types can
# share one bucket (e.g. "EC2 - Other"), so never assume 1:1.
CE_SERVICE_MAP = {
    "EC2Instance": ("Amazon Elastic Compute Cloud - Compute",),
    "EBSVolume": ("EC2 - Other",),
    "ElasticIP": ("EC2 - Other",),
    "NatGateway": ("EC2 - Other",),
    "VpcEndpoint": ("Amazon Virtual Private Cloud",),
    "LoadBalancer": ("Amazon Elastic Load Balancing",),
    "RDSInstance": ("Amazon Relational Database Service",),
    "RDSCluster": ("Amazon Relational Database Service",),
    "DocumentDBCluster": ("Amazon DocumentDB (with MongoDB compatibility)",),
    "DocumentDBInstance": ("Amazon DocumentDB (with MongoDB compatibility)",),
    "NeptuneCluster": ("Amazon Neptune",),
    "NeptuneInstance": ("Amazon Neptune",),
    "EFSFileSystem": ("Amazon Elastic File System",),
    "ElastiCacheCluster": ("Amazon ElastiCache",),
    "OpenSearchDomain": ("Amazon OpenSearch Service",),
    "OpenSearchServerlessCollection": ("Amazon OpenSearch Service",),
    "KMSKey": ("AWS Key Management Service",),
    "Secret": ("AWS Secrets Manager",),
    "ACMCertificate": ("AWS Certificate Manager",),
    "SSMParameter": ("AWS Systems Manager",),
    "Route53HostedZone": ("Amazon Route 53",),
    "Route53ResolverEndpoint": ("Amazon Route 53",),
    "CloudFrontDistribution": ("Amazon CloudFront",),
    "ECRRepository": ("Amazon EC2 Container Registry (ECR)",),
    "StepFunctionsStateMachine": ("AWS Step Functions",),
    "ECSService": ("Amazon Elastic Container Service",),
    "CodeBuildProject": ("AWS CodeBuild",),
    "CodePipeline": ("AWS CodePipeline",),
    "GlueJob": ("AWS Glue",),
    "GlueCrawler": ("AWS Glue",),
    "BackupVault": ("AWS Backup",),
    "SageMakerEndpoint": ("Amazon SageMaker",),
    "EKSCluster": ("Amazon Elastic Kubernetes Service",),
    "RedshiftCluster": ("Amazon Redshift",),
    "RedshiftServerlessWorkgroup": ("Amazon Redshift",),
    "WAFWebACL": ("AWS WAF",),
    "NetworkFirewall": ("AWS Network Firewall",),
    "TransitGateway": ("Amazon Virtual Private Cloud",),
    "VPNConnection": ("Amazon Virtual Private Cloud",),
    "SageMakerNotebook": ("Amazon SageMaker",),
    "SageMakerStudioApp": ("Amazon SageMaker",),
    "SageMakerTrainingJob": ("Amazon SageMaker",),
    "RDSSnapshot": ("Amazon Relational Database Service",),
    "LambdaFunction": ("AWS Lambda",),
    "DynamoDBTable": ("Amazon DynamoDB",),
    "S3Bucket": ("Amazon Simple Storage Service",),
    "CloudWatchLogGroup": ("AmazonCloudWatch",),
    "APIGatewayRestApi": ("Amazon API Gateway",),
    "APIGatewayV2Api": ("Amazon API Gateway",),
    "AppSyncApi": ("AWS AppSync",),
    "EBSSnapshot": ("EC2 - Other",),
    "SQSQueue": ("Amazon Simple Queue Service",),
    "KinesisStream": ("Amazon Kinesis",),
    "FirehoseStream": ("Amazon Kinesis Firehose",),
    "MSKCluster": ("Amazon Managed Streaming for Apache Kafka",),
    "SNSTopic": ("Amazon Simple Notification Service",),
    "EventBridgeRule": ("Amazon EventBridge",),
    "EventBridgeSchedule": ("Amazon EventBridge",),
}

# Cost Explorer service -> (types this scan collects for it, types it bills that are
# not collected). A billed service with no entry is reported as unsupported.
BILLING_COVERAGE = {
    "Amazon Elastic Compute Cloud - Compute": (
        ("EC2Instance", "ReservedInstance", "SpotInstanceRequest"), ()),
    "EC2 - Other": (
        ("EBSVolume", "EBSSnapshot", "ElasticIP", "AMI", "NatGateway"),
        ("inter-AZ and internet data transfer",)),
    # VPC endpoints and public IPv4 bill here, not under "EC2 - Other".
    "Amazon Virtual Private Cloud": (
        ("VpcEndpoint", "TransitGateway", "VPNConnection"),
        ("public IPv4 addresses", "VPC Lattice", "traffic mirroring")),
    "Amazon Elastic Load Balancing": (("LoadBalancer",), ()),
    "Amazon Relational Database Service": (
        ("RDSInstance", "RDSCluster", "RDSSnapshot"), ("automated backup storage",)),
    "AWS Lambda": (("LambdaFunction",), ()),
    "Amazon Elastic File System": (("EFSFileSystem",), ()),
    "Amazon ElastiCache": (("ElastiCacheCluster",), ("backup storage",)),
    "Amazon OpenSearch Service": (("OpenSearchDomain", "OpenSearchServerlessCollection"), ()),
    "AWS Key Management Service": (("KMSKey",), ("API requests",)),
    "AWS Secrets Manager": (("Secret",), ()),
    "AWS Certificate Manager": (("ACMCertificate",), ()),
    "AWS Systems Manager": (("SSMParameter",), ("Parameter Store API interactions", "other Systems Manager features")),
    # Domain registration bills separately, as "Amazon Registrar".
    "Amazon Route 53": (("Route53HostedZone", "Route53ResolverEndpoint"),
                        ("DNS queries", "health checks", "Resolver DNS Firewall and query logging")),
    "Amazon CloudFront": (("CloudFrontDistribution",), ()),
    "Amazon EC2 Container Registry (ECR)": (("ECRRepository",), ()),
    "AWS Step Functions": (("StepFunctionsStateMachine",), ()),
    "Amazon Elastic Container Service": (("ECSService", "ECSCluster"), ("standalone tasks run outside a service",)),
    "AWS CodeBuild": (("CodeBuildProject",), ()),
    "AWS CodePipeline": (("CodePipeline",), ()),
    "AWS Glue": (("GlueJob", "GlueCrawler"), ("Data Catalog storage and requests",)),
    "AWS Backup": (("BackupVault", "BackupPlan"), ()),
    "Amazon Elastic Kubernetes Service": (("EKSCluster",), ("EKS add-ons billed separately",)),
    "Amazon Redshift": (("RedshiftCluster", "RedshiftServerlessWorkgroup"), ("Redshift Spectrum",)),
    "AWS WAF": (("WAFWebACL",), ()),
    "Amazon Kinesis": (("KinesisStream",), ()),
    "Amazon Kinesis Firehose": (("FirehoseStream",), ()),
    "Amazon Managed Streaming for Apache Kafka": (("MSKCluster",), ("MSK Connect connectors",)),
    "AWS Network Firewall": (("NetworkFirewall",), ()),
    "Amazon SageMaker": (("SageMakerEndpoint", "SageMakerNotebook", "SageMakerStudioApp",
                          "SageMakerTrainingJob"), ("processing jobs",)),
    "Amazon DocumentDB (with MongoDB compatibility)": (
        ("DocumentDBCluster", "DocumentDBInstance"), ("snapshot storage",)),
    "Amazon Neptune": (("NeptuneCluster", "NeptuneInstance"), ("snapshot storage",)),
    "Amazon DynamoDB": (("DynamoDBTable",), ("backup and restore storage",)),
    "Amazon Simple Storage Service": (("S3Bucket",), ()),
    "AmazonCloudWatch": (
        ("CloudWatchLogGroup", "CloudWatchAlarm"),
        ("custom metrics", "dashboards", "Contributor Insights")),
    "Amazon API Gateway": (("APIGatewayRestApi", "APIGatewayV2Api"), ()),
    "AWS AppSync": (("AppSyncApi",), ("Event APIs",)),
    "Amazon Simple Queue Service": (("SQSQueue",), ()),
    "Amazon Simple Notification Service": (("SNSTopic",), ("SMS delivery",)),
    "Amazon EventBridge": (
        ("EventBridgeRule", "EventBridgeSchedule", "EventBridgeBus"), ()),
    "AWS CloudTrail": (("CloudTrailTrail",), ("data events", "CloudTrail Lake")),
    "AWS Amplify": (("AmplifyApp",), ()),
    "Amazon Cognito": (("CognitoUserPool",), ("identity pools",)),
    "AWS X-Ray": (("XRayGroup", "XRaySamplingRule"), ()),
    "AWS Elastic Beanstalk": (("ElasticBeanstalkEnvironment",
                               "ElasticBeanstalkApplication"), ()),
}

# Billed lines that are not resource types: reported as spend, not as coverage gaps.
BILLING_NON_RESOURCE_SERVICES = (
    "Tax", "Refund", "Credit", "AWS Support (Developer)",
    "AWS Support (Business)", "AWS Support (Enterprise)",
    "AWS Cost Explorer", "AWS Data Transfer", "Savings Plans for AWS Compute usage",
)

# Billing findings below this are noise; they are still counted in a footnote.
BILLING_FINDING_MIN_AMOUNT = Decimal("0.01")

# Usage-type fragment -> (what it is, whether this scan collects it).
# ORDER MATTERS: the first match wins, so specific fragments come first.
USAGE_TYPE_KINDS = (
    ("NatGateway", "NAT gateway", True),
    ("VpcEndpoint", "VPC endpoint", True),
    ("PrivateLink", "PrivateLink endpoint", True),
    ("TransitGateway", "transit gateway", True),
    ("VPN", "VPN connection", True),
    ("EBS:SnapshotUsage", "EBS snapshot storage", True),
    ("EBS:VolumeP-IOPS", "EBS provisioned IOPS", True),
    ("EBS:VolumeIOUsage", "EBS volume I/O", True),
    ("EBS:VolumeUsage", "EBS volume storage", True),
    ("EBS:", "EBS storage", True),
    ("EBSOptimized", "EBS-optimised instance hours", True),
    ("PublicIPv4", "public IPv4 address", True),
    ("IdleAddress", "unattached Elastic IP", True),
    ("ElasticIP", "Elastic IP", True),
    ("DataTransfer", "data transfer", False),
    ("Regional-Bytes", "inter-AZ data transfer", False),
    ("In-Bytes", "data transfer in", False),
    ("Out-Bytes", "data transfer out", False),
    ("TimedStorage", "stored data", True),
    ("TimedBackupStorage", "backup storage", False),
    ("BackupUsage", "backup storage", False),
    ("PaidEventsRecorded", "CloudTrail data events", False),
    ("FreeEventsRecorded", "CloudTrail management events", True),
    # The Phase B bill lines that declare gaps. Kept after the backup entries,
    # which must win for "ElastiCache:BackupUsage" and friends.
    ("HostedZone", "Route 53 hosted zone", True),
    ("DNS-Queries", "DNS queries", False),
    ("Health-Check", "Route 53 health checks", False),
    ("ResolverNetworkInterface", "Route 53 Resolver endpoint", True),
    ("Resolver", "Route 53 Resolver", False),
    ("KMS-Keys", "KMS key-months", True),
    ("KMS-Requests", "KMS API requests", False),
    ("NodeUsage", "cache node hours", True),
    ("ElastiCache:", "ElastiCache serverless usage", True),
    ("IndexingOCU", "OpenSearch Serverless indexing", True),
    ("SearchOCU", "OpenSearch Serverless search", True),
    ("ESInstance", "OpenSearch instance hours", True),
    ("ES:", "OpenSearch storage", True),
    ("Fargate", "Fargate task hours", True),
    ("AmazonEKS-Hours", "EKS control plane hours", True),
    ("RedshiftServerless", "Redshift Serverless", True),
    ("Node:", "Redshift node hours", True),
    ("RMS:", "Redshift managed storage", True),
    ("Notebk", "SageMaker notebook hours", True),
    ("Host:", "SageMaker endpoint hours", True),
    ("Train:", "SageMaker training", True),
    ("Studio", "SageMaker Studio", True),
)

# Usage-type fragment -> the resource types it is the standing charge for.
# Account-wide types only: a usage-type line carries no region.
USAGE_TYPE_RESOURCE_TYPES = {
    "HostedZone": ("Route53HostedZone",),
}

# Minutes a billing response is reused (Cost Explorer charges per request).
# Deliberately not configurable; the report shows the observation time instead.
BILLING_CACHE_TTL_MINUTES = 360
