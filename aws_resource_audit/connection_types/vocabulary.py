"""The vocabulary every connection type is declared in: states, confidence,
semantics, ownership, and the ConnectionType record itself."""

from dataclasses import dataclass

# Connection types: every way a link can be inferred, id "<source>.<target>.<mechanism>".
# "on" may merge projects, "report-only" only shows the link, "off" skips detection.

CONN_ON = "on"
CONN_REPORT_ONLY = "report-only"
CONN_OFF = "off"
CONN_ALL_STATES = (CONN_ON, CONN_REPORT_ONLY, CONN_OFF)

# Confidence, strongest first: authoritative (an AWS API says so), config reference,
# heuristic (name resemblance). authoritative_only keeps just the first.
CONF_AUTHORITATIVE = "authoritative"
CONF_CONFIG_REFERENCE = "config reference"
CONF_HEURISTIC = "heuristic"

# How each confidence is spelled in the connections column. LOAD-BEARING: must match
# audit_groups' WEAK_EVIDENCE_MARKERS.
CONFIDENCE_DISPLAY = {
    CONF_AUTHORITATIVE: "authoritative",
    CONF_CONFIG_REFERENCE: "config reference",
    CONF_HEURISTIC: "LOW CONFIDENCE name-match heuristic",
}

# Sentinel for mechanisms whose target type is discovered at runtime.
ANY_TARGET = "*"

# What a link means: descriptive only. Grouping is decided by `ownership`.
SEM_OWNS = "owns"                          # created it, contains it
SEM_DEPLOYED_FROM = "deployed_from"        # a deployment produced it
SEM_INVOKES = "invokes"                    # calls it at runtime
SEM_READS_OR_WRITES = "reads_or_writes"    # uses it as data
SEM_RUNS_AS = "runs_as"                    # assumes this identity
SEM_PERMITS_ACCESS_TO = "permits_access_to"  # a policy names it
SEM_USES_IMAGE = "uses_image"              # launched from it
SEM_SHARES_NETWORK = "shares_network"      # same VPC/subnet/security group
SEM_NAMES = "names"                        # resemblance only
SEM_REFERENCES = "references"              # generic, when none of the above fits

# How much a link may claim about ownership.
OWNERSHIP_OWNS = "ownership"
OWNERSHIP_SUPPORTING = "supporting"
OWNERSHIP_NONE = "none"
OWNERSHIP_LEVELS = (OWNERSHIP_OWNS, OWNERSHIP_SUPPORTING, OWNERSHIP_NONE)


@dataclass(frozen=True)
class ConnectionType:
    """One declared way of inferring a link. `targets` documents what it can resolve
    to; the per-edge type guard is add_edge's `target_service`."""
    id: str
    source: str
    targets: tuple
    mechanism: str
    confidence: str
    description: str
    default_state: str = CONN_ON
    kind: str = "edge"  # "edge" | "grouping"
    # Only ownership may drive grouping: OWNS assigns a project, SUPPORTING suggests one,
    # NONE (the default) says nothing, so a new type cannot merge projects by accident.
    semantics: str = "references"
    ownership: str = "none"
    # Where a bare-name target may live: "regional" (default, conservative) or "account".
    # An ARN carries its own scope.
    target_scope: str = "regional"


# Every service probable_resource_id_from_arn can map back to a scanned row.
ARN_RESOLVABLE_TARGETS = (
    "S3Bucket", "DynamoDBTable", "LambdaFunction", "RDSInstance",
    "CloudWatchLogGroup", "EC2Instance", "EBSVolume", "SecurityGroup",
    "ElasticIP", "KeyPair", "AMI", "EBSSnapshot", "NetworkInterface",
    "SQSQueue", "SNSTopic", "IAMRole",
    "VPC", "Subnet", "RouteTable", "InternetGateway", "NatGateway", "VpcEndpoint",
    "RDSCluster", "RDSSnapshot", "EFSFileSystem", "KMSKey", "Secret", "ECRRepository",
    "CloudFrontDistribution", "AutoScalingGroup", "TargetGroup", "StepFunctionsStateMachine",
    "ECSCluster", "ECSService", "CodeBuildProject", "SageMakerEndpoint", "EKSCluster",
    "EventBridgeBus", "WAFWebACL", "NetworkFirewall", "APIGatewayDomainName",
    "CognitoUserPool", "KinesisStream", "FirehoseStream", "MSKCluster",
    "GlueJob", "GlueCrawler", "TransitGateway", "VPNConnection",
    "LoadBalancer", "SSMParameter", "AppSyncApi", "OpenSearchServerlessCollection",
    "Route53ResolverEndpoint",
)

# Types whose name is unique across all of AWS, so a reference to one matches it in any
# region, whatever the connection type's target_scope. See docs/decisions/0062.
GLOBALLY_NAMED_TARGETS = ("S3Bucket",)
