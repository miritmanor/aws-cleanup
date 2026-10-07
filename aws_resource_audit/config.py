"""Tunable thresholds and the classification tables the whole audit reads.
The run's clock is now(), not a constant, so an import cannot freeze it."""

from datetime import datetime, timezone

from botocore.config import Config

# The single instant the run is measured against. An accessor, since an imported
# constant goes stale; frozen on first use so every region shares one clock.
_NOW = None


def now():
    """The instant this run is measured against, frozen on first call."""
    global _NOW
    if _NOW is None:
        _NOW = datetime.now(timezone.utc)
    return _NOW


def set_now(value):
    """Pin the run's clock. Called once at the top of run(); tests use it to
    make anything derived from the current time reproducible."""
    global _NOW
    _NOW = value


# The overview's metric ids, here so settings.py can validate them. A test keeps
# them in step with present/bubbles.
BUBBLE_METRIC_IDS = ("resource_count", "unused_pct", "unused_count", "cost")

STALE_THRESHOLD_DAYS = 730  # 2 years, used only for the final "flag" column
CW_LOOKBACK_DAYS = 455      # ~15 months, the practical CloudWatch ceiling
COST_LOOKBACK_DAYS = 30     # Cost Explorer window for the cost columns

# Guards on prefix-wildcard grants: a too-short prefix, or one matching too many
# rows, is a blanket grant over a naming convention, not evidence of use.
ARN_PREFIX_MIN_LEN = 4
ARN_PREFIX_MAX_MATCHES = 12

# Retries with backoff so a burst of per-resource API calls (CloudWatch in
# particular) doesn't silently come back empty under throttling.
RETRY_CONFIG = Config(retries={"total_max_attempts": 6, "mode": "adaptive"})

# Tag keys, case-sensitive, first match wins. Only PROJECT_TAG_KEYS decides
# ownership; environment and component tags are recorded but never merge projects.
PROJECT_TAG_KEYS = [
    "Project", "project", "App", "app", "Application", "application",
]
ENVIRONMENT_TAG_KEYS = ["Environment", "environment", "Env", "env"]
COMPONENT_TAG_KEYS = ["Service", "service"]

# The union of every tag we read. NOT the project-identity list.
GROUP_TAG_KEYS = PROJECT_TAG_KEYS + ENVIRONMENT_TAG_KEYS + COMPONENT_TAG_KEYS

# Shared plumbing (one SG or key pair reused across unrelated projects): its edges
# never merge projects, and the graph contracts it away. An AMI is not one.
NON_BRIDGING_SERVICES = frozenset({"SecurityGroup", "KeyPair"})

# Most listings are exhaustive, so a referenced resource missing from one is gone.
# These listings are filtered; add any new filtered collector here.
FILTERED_ENUMERATIONS = frozenset({"AMI", "EBSSnapshot", "IAMPolicy"})

# Per metric: True if an empty series means idle (STALE). A metric missing here
# yields UNKNOWN - one that may be disabled cannot condemn a resource.
METRIC_ABSENCE_MEANS_IDLE = {
    # Published per invocation/request. No datapoints over 15 months means
    # nothing invoked it in 15 months.
    "Invocations": True,
    "Count": True,
    "RequestCount": True,
    "NumberOfMessagesSent": True,
    "NumberOfMessagesReceived": True,
    "NumberOfMessagesPublished": True,
    "ConsumedReadCapacityUnits": True,
    "ConsumedWriteCapacityUnits": True,
    # Route 53 publishes it only for a zone that received a query.
    "DNSQueries": True,
    # Emitted continuously while running, so an empty series is ambiguous.
    "CPUUtilization": False,
    "DatabaseConnections": False,
    "VolumeReadOps": False,
    "VolumeWriteOps": False,
}

# What the graph draws: AWS's application categories minus build artifacts; the
# rest becomes "via" links. tests/test_service_map.py holds the set to that rule.
STRUCTURAL_SERVICES = frozenset({
    # Compute - the things that run code
    "EC2Instance", "LambdaFunction", "ElasticBeanstalkEnvironment", "AutoScalingGroup",
    # Storage and databases - the things that hold state
    "S3Bucket", "EBSVolume", "EFSFileSystem", "RDSInstance", "RDSCluster", "DynamoDBTable",
    "DocumentDBCluster", "DocumentDBInstance", "NeptuneCluster", "NeptuneInstance",
    "ElastiCacheCluster",
    # Analytics to AWS, but an application queries it like a database.
    "OpenSearchDomain", "OpenSearchServerlessCollection",
    # Analytics to AWS, but a stream or Kafka cluster carries an application's events like a queue.
    "KinesisStream", "FirehoseStream", "MSKCluster",
    # Networking to AWS, but a front door in the same sense as a load balancer.
    "CloudFrontDistribution",
    # Containers to AWS, but a service is what runs, like an instance or function.
    "ECSService",
    # Machine Learning to AWS, but an application calls an inference endpoint like an API.
    "SageMakerEndpoint",
    # Containers to AWS, but the cluster is where the application's workloads run.
    "EKSCluster",
    "RedshiftCluster", "RedshiftServerlessWorkgroup",
    # Front-end - how the outside world reaches it
    "AmplifyApp", "APIGatewayRestApi", "APIGatewayV2Api", "APIGatewayDomainName", "AppSyncApi",
    # Application integration - how the pieces reach each other
    "SQSQueue", "SNSTopic", "StepFunctionsStateMachine",
    # Rules and schedules are what make a resource run, so they stay visible.
    "EventBridgeRule", "EventBridgeSchedule", "EventBridgeBus",
    # The one exception to the category rule: an ALB is a front door, like API Gateway.
    "LoadBalancer",
})

# Everything else, listed explicitly: the coverage guard fails until a new type is classified.
SUPPORTING_SERVICES = frozenset({
    "IAMRole", "IAMPolicy", "IAMUser", "IAMGroup", "IAMRoleUnusedAccessFinding",
    "CognitoUserPool", "SecurityGroup", "KeyPair", "NetworkInterface",
    "VPC", "Subnet", "RouteTable", "InternetGateway", "NatGateway", "VpcEndpoint",
    # A backup of a database, not something the application talks to - like EBSSnapshot.
    "RDSSnapshot", "KMSKey", "Secret", "ACMCertificate", "SSMParameter", "Route53HostedZone",
    "Route53ResolverEndpoint", "WAFWebACL", "NetworkFirewall",
    "TransitGateway", "VPNConnection",
    # Where deployments pull images from; the running service is what is drawn.
    "ECRRepository", "TargetGroup", "ECSCluster", "CodeBuildProject", "CodePipeline",
    # Analytics to AWS: batch pipelines around the application, not part of its request path.
    "GlueJob", "GlueCrawler",
    # Storage to AWS, but backups are not something the application talks to.
    "BackupVault", "BackupPlan",
    # A notebook or Studio app is a workbench a person uses, not something the
    # application calls; a training job produces a model and then ends.
    "SageMakerNotebook", "SageMakerStudioApp", "SageMakerTrainingJob",
    "ElasticIP", "LaunchTemplate", "SpotInstanceRequest", "ReservedInstance",
    "AMI", "EBSSnapshot", "CloudWatchLogGroup", "CloudWatchAlarm",
    "CloudTrailTrail", "CloudFormationStack", "XRayGroup", "XRaySamplingRule",
    # Not drawn: the environment is what runs; the application is only the version
    # container it was deployed from.
    "ElasticBeanstalkApplication",
})

# --- Name-token grouping: when a word shared by two resource names is distinctive
# enough to trust, since often no API link exists between them.
NAME_TOKEN_MIN_LEN = 4        # ignore tokens shorter than this outright
NAME_TOKEN_MIN_SUBSTRING = 6  # ...and require this much before matching INSIDE
                              # a longer token ("moonpie" in "moonpiekeypair"),
                              # which is where false positives come from
NAME_TOKEN_MAX_SHARE = 0.10   # a token appearing in more than this share of all
                              # rows is a house style, not a project name - it is
                              # refused and listed in the grouping audit

# Words that carry no project identity (AWS vocabulary, environments, generic
# nouns); without these, "role" or "lambda" would merge most of an account.
NAME_TOKEN_STOPWORDS = frozenset("""
role roles policy policies user users group groups pool userpool identity
lambda awslambda function functions execution executions handler
amplify amplifyapp app apps backend frontend deployment deploy
cognito auth unauth authenticated unauthenticated login logout signin signup
dynamo dynamodb table tables bucket buckets storage store stores
apigateway gateway api apis rest http https ssh rdp endpoint
cloud cloudformation cloudwatch cloudtrail watch logs log trail stack stacks
event events bridge eventbridge scheduler schedule rule rules trigger triggers
sns sqs sqs queue topic destination notification notifications
aws amazon service services microservice serverless managed
s3 ec2 vpc iam rds sso ssm kms elb efs ecs eks arn ses ecr asg
default main master primary secondary basic full admin admins root
test tests testing staging stage prod production dev development sandbox demo
temp tmp old new backup backups archive copy
launch wizard instance instances server servers node nodes cluster
security group keypair key pair keys
ubuntu linux windows amazonlinux debian centos python java nodejs
east west north south central region regions global zone
access read write readwrite readonly allow deny grant permission permissions
update updates create delete remove list describe
upload uploads download downloads manage manager management
with without from into for the and version latest
device data runtime build builds output input
image images snapshot snapshots volume volumes
error errors reporting report metrics metric alarm alarms flowlogs
custom common shared general utils util helper helpers lib core
elastic beanstalk cache balancing loadbalancing harness poller
analyzer advisor explorer organizations support trusted bots
""".split())

# Re-exported so existing `from .config import ...` keeps working.
from .billing_tables import (  # noqa: E402,F401
    CE_SERVICE_MAP,
    BILLING_COVERAGE,
    BILLING_NON_RESOURCE_SERVICES,
    BILLING_FINDING_MIN_AMOUNT,
    USAGE_TYPE_KINDS,
    USAGE_TYPE_RESOURCE_TYPES,
    BILLING_CACHE_TTL_MINUTES,
)

# What keeping each type costs: "cost" (billed for existing), "usage" (billed when
# used), "indirect" (keeps billable things alive) or "free". Charging models, not your bill.
SERVICE_BILLING = {
    "EC2Instance":                ("usage",    "per-hour while running; a stopped instance is free but its EBS volumes are not"),
    "EBSVolume":                  ("cost",     "billed per provisioned GB whether or not it is attached"),
    "EBSSnapshot":                ("cost",     "billed per GB of snapshot storage"),
    "AMI":                        ("indirect", "the AMI is free; the EBS snapshots backing it are billed"),
    "ReservedInstance":           ("cost",     "prepaid or committed capacity - charged whether or not you use it"),
    "ElasticIP":                  ("cost",     "every public IPv4 address you hold is billed hourly"),
    "NetworkInterface":           ("free",     "the ENI is free; any public IPv4 associated with it is billed"),
    "VPC":                        ("free",     "a VPC is free; NAT gateways, endpoints and public IPv4 inside it are not"),
    "Subnet":                     ("free",     "subnets are free"),
    "RouteTable":                 ("free",     "route tables are free"),
    "InternetGateway":            ("free",     "the gateway is free; data out through it is billed as EC2 data transfer"),
    "NatGateway":                 ("cost",     "billed hourly while it exists plus per GB processed, used or not"),
    "VpcEndpoint":                ("cost",     "interface endpoints bill per AZ-hour plus per GB; S3/DynamoDB gateway endpoints are free"),
    "SecurityGroup":              ("free",     "no charge"),
    "KeyPair":                    ("free",     "no charge"),
    "LaunchTemplate":             ("free",     "no charge"),
    "SpotInstanceRequest":        ("free",     "the request is free; the instance it launches is billed"),
    "LoadBalancer":               ("cost",     "billed hourly even with no traffic, plus capacity units"),
    "RDSInstance":                ("cost",     "billed hourly while it exists; storage billed even when stopped"),
    "RDSCluster":                 ("cost",     "cluster storage and I/O billed even with no instance running"),
    "DocumentDBCluster":          ("cost",     "cluster storage and I/O billed even with no instance running"),
    "DocumentDBInstance":         ("cost",     "billed hourly while it exists"),
    "NeptuneCluster":             ("cost",     "cluster storage and I/O billed even with no instance running"),
    "NeptuneInstance":            ("cost",     "billed hourly while it exists"),
    "EFSFileSystem":              ("cost",     "billed per GB stored, mounted or not"),
    "ElastiCacheCluster":         ("cost",     "billed per node-hour while it exists, used or not"),
    "OpenSearchDomain":           ("cost",     "billed per instance-hour plus storage, searched or not"),
    "OpenSearchServerlessCollection": ("cost", "per OCU-hour, with a regional minimum while any collection exists"),
    "KMSKey":                     ("cost",     "about $1/month per customer-managed key, enabled or disabled"),
    "Secret":                     ("cost",     "about $0.40/month per secret, plus API calls"),
    "SSMParameter":               ("cost",     "about $0.05/month per advanced-tier parameter, read or not"),
    "ACMCertificate":             ("free",     "public certificates are free; private-CA ones bill monthly"),
    "Route53HostedZone":          ("cost",     "about $0.50/month per hosted zone, plus queries"),
    "Route53ResolverEndpoint":    ("cost",     "about $90/month per IP address (two at least), plus queries"),
    "CloudFrontDistribution":     ("usage",    "billed per request and GB served; a disabled one costs nothing"),
    "ECRRepository":              ("cost",     "images billed per GB-month stored"),
    "AutoScalingGroup":           ("indirect", "free itself; the instances it keeps running bill"),
    "TargetGroup":                ("free",     "target groups are free"),
    "StepFunctionsStateMachine":  ("usage",    "billed per state transition or request; idle costs nothing"),
    "ECSCluster":                 ("free",     "a cluster is free; its tasks and container instances bill"),
    "ECSService":                 ("usage",    "Fargate tasks bill while running; a service at zero is free"),
    "CodeBuildProject":           ("usage",    "billed per build minute; an unused project is free"),
    "GlueJob":                    ("usage",    "per DPU-hour while a run is in progress"),
    "GlueCrawler":                ("usage",    "per DPU-hour while it crawls; scheduled ones bill every run"),
    "CodePipeline":               ("cost",     "V1 pipelines bill monthly while active; V2 per action minute"),
    "BackupVault":                ("cost",     "recovery points billed per GB-month; an empty vault is free"),
    "BackupPlan":                 ("indirect", "free itself; the recovery points it creates bill"),
    "SageMakerEndpoint":          ("cost",     "billed per instance-hour while in service; serverless per request"),
    "EventBridgeBus":             ("usage",    "custom events billed per million published; an idle bus is free"),
    "WAFWebACL":                  ("cost",     "about $5/month per web ACL plus rules and requests, used or not"),
    "TransitGateway":             ("cost",     "about $36/month per attachment plus per GB, used or not"),
    "VPNConnection":              ("cost",     "about $36/month per connection while it exists, tunnels up or not"),
    "NetworkFirewall":            ("cost",     "about $290/month per endpoint (one per AZ) plus per GB, used or not"),
    "SageMakerNotebook":          ("cost",     "billed per hour while in service; storage while stopped"),
    "SageMakerStudioApp":         ("cost",     "per hour on an instance while in service; the system size is free"),
    "SageMakerTrainingJob":       ("usage",    "per instance-second while it runs; nothing once finished"),
    "EKSCluster":                 ("cost",     "about $73/month for the control plane, plus its nodes"),
    "RedshiftCluster":            ("cost",     "billed per node-hour while running; storage even when paused"),
    "RedshiftServerlessWorkgroup": ("usage",   "per RPU-second while queries run; storage always"),
    "RDSSnapshot":                ("cost",     "manual snapshots are billed per GB-month until deleted"),
    "DynamoDBTable":              ("usage",    "on-demand is per-request; provisioned capacity is billed hourly"),
    "KinesisStream":              ("cost",     "per shard-hour (provisioned) or stream-hour (on-demand), idle or not"),
    "MSKCluster":                 ("cost",     "per broker-hour plus storage, produced to or not"),
    "FirehoseStream":             ("usage",    "per GB ingested; an idle delivery stream is free"),
    "SQSQueue":                   ("usage",    "billed per request; an idle queue is free, but a queue holding undelivered messages usually means its consumer is gone"),
    "SNSTopic":                   ("usage",    "billed per publish and per delivery; an idle topic is free"),
    "EventBridgeRule":            ("free",     "the rule itself is free; custom-bus events and some targets are billed per million"),
    "EventBridgeSchedule":        ("usage",    "billed per invocation beyond the free allowance; a disabled schedule costs nothing"),
    "S3Bucket":                   ("cost",     "billed per GB stored - an empty bucket is free"),
    "CloudWatchLogGroup":         ("cost",     "billed per GB stored; retention set to Never expire keeps paying"),
    "LambdaFunction":             ("usage",    "per invocation and duration; an idle function is free"),
    "APIGatewayRestApi":          ("usage",    "per request; an idle API is free"),
    "APIGatewayV2Api":            ("usage",    "per request; an idle API is free"),
    "AppSyncApi":                 ("usage",    "per query and per real-time minute; an idle API is free"),
    "APIGatewayDomainName":       ("free",     "a custom domain name is free; requests bill on the API"),
    "CloudWatchAlarm":            ("cost",     "billed per alarm per month beyond the free allowance"),
    "CloudTrailTrail":            ("usage",    "one management-event trail is free; extra trails and data events are billed"),
    "AmplifyApp":                 ("usage",    "billed for build minutes and hosting traffic"),
    "CognitoUserPool":            ("usage",    "billed per monthly active user beyond the free tier"),
    "XRayGroup":                  ("usage",    "billed per trace recorded and scanned"),
    "XRaySamplingRule":           ("free",     "no charge for the rule itself"),
    "CloudFormationStack":        ("free",     "no charge for the stack; the resources it creates are billed"),
    "ElasticBeanstalkEnvironment": ("indirect", "the environment itself is free; the EC2 instances, load balancer, and any RDS instance behind it are billed separately"),
    "ElasticBeanstalkApplication": ("indirect", "the application is free; the versions and folders it keeps in the Elastic Beanstalk service bucket are billed as S3 storage"),
    "IAMUser":                    ("free",     "no charge"),
    "IAMRole":                    ("free",     "no charge"),
    "IAMGroup":                   ("free",     "no charge"),
    "IAMPolicy":                  ("free",     "no charge"),
    "IAMRoleUnusedAccessFinding": ("free",     "an IAM Access Analyzer finding, not a billable resource"),
}
