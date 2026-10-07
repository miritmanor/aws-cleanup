"""ARNs back to the resource id and type this scan keys rows on."""

from ..config import ARN_PREFIX_MIN_LEN

# EC2 ARN resource-type prefix -> internal service name, so an extracted id always
# carries the type that prevents cross-type collisions.
EC2_ARN_PREFIX_TO_SERVICE = {
    "instance": "EC2Instance", "volume": "EBSVolume", "security-group": "SecurityGroup",
    "elastic-ip": "ElasticIP", "key-pair": "KeyPair", "image": "AMI", "snapshot": "EBSSnapshot",
    "network-interface": "NetworkInterface", "vpc": "VPC", "subnet": "Subnet",
    "route-table": "RouteTable", "internet-gateway": "InternetGateway",
    "natgateway": "NatGateway", "vpc-endpoint": "VpcEndpoint",
    "transit-gateway": "TransitGateway", "vpn-connection": "VPNConnection",
}


def _resource_id_and_service_from_arn(arn):
    """Pull the resource id and internal service out of an ARN, without judging
    whether the id is specific enough to use."""
    if not arn or not arn.startswith("arn:"):
        return None, None
    parts = arn.split(":", 5)
    if len(parts) < 6:
        return None, None
    service, resource_part = parts[2], parts[5]
    rid, target_service = None, None
    if service == "s3":
        rid, target_service = resource_part.split("/")[0], "S3Bucket"
    elif service == "elasticloadbalancing" and resource_part.startswith("targetgroup/"):
        rid, target_service = resource_part.split("/")[1], "TargetGroup"
    elif service == "elasticloadbalancing" and resource_part.startswith("loadbalancer/"):
        # loadbalancer/app/<name>/<id> or loadbalancer/net/...; a Classic one is loadbalancer/<name>.
        parts = resource_part.split("/")
        rid, target_service = (parts[2] if len(parts) >= 4 else parts[1]), "LoadBalancer"
    elif service == "autoscaling" and "autoScalingGroupName/" in resource_part:
        rid, target_service = resource_part.split("autoScalingGroupName/", 1)[1], "AutoScalingGroup"
    elif service == "ecr" and resource_part.startswith("repository/"):
        rid, target_service = resource_part[len("repository/"):], "ECRRepository"
    elif service == "cloudfront" and resource_part.startswith("distribution/"):
        rid, target_service = resource_part[len("distribution/"):], "CloudFrontDistribution"
    elif service == "kms" and resource_part.startswith("key/"):
        rid, target_service = resource_part[len("key/"):], "KMSKey"
    elif service == "secretsmanager" and resource_part.startswith("secret:"):
        # AWS appends "-" and six random characters to the name in the ARN.
        rid, target_service = resource_part[len("secret:"):].rsplit("-", 1)[0], "Secret"
    elif service == "elasticfilesystem" and resource_part.startswith("file-system/"):
        rid, target_service = resource_part[len("file-system/"):], "EFSFileSystem"
    elif service == "dynamodb" and resource_part.startswith("table/"):
        rid, target_service = resource_part[len("table/"):].split("/")[0], "DynamoDBTable"
    elif service == "lambda" and resource_part.startswith("function:"):
        rid, target_service = resource_part[len("function:"):].split(":")[0], "LambdaFunction"
    elif service == "rds" and resource_part.startswith("db:"):
        rid, target_service = resource_part[len("db:"):], "RDSInstance"
    elif service == "rds" and resource_part.startswith("cluster:"):
        rid, target_service = resource_part[len("cluster:"):], "RDSCluster"
    elif service == "rds" and resource_part.split(":")[0] in ("snapshot", "cluster-snapshot"):
        rid, target_service = resource_part.split(":", 1)[1], "RDSSnapshot"
    elif service == "logs" and resource_part.startswith("log-group:"):
        rid, target_service = resource_part[len("log-group:"):].split(":")[0], "CloudWatchLogGroup"
    elif service == "apigateway" and resource_part.startswith("/domainnames/"):
        rid, target_service = resource_part[len("/domainnames/"):].split("/")[0], "APIGatewayDomainName"
    elif service == "cognito-idp" and resource_part.startswith("userpool/"):
        rid, target_service = resource_part[len("userpool/"):], "CognitoUserPool"
    elif service == "kinesis" and resource_part.startswith("stream/"):
        rid, target_service = resource_part[len("stream/"):], "KinesisStream"
    elif service == "firehose" and resource_part.startswith("deliverystream/"):
        rid, target_service = resource_part[len("deliverystream/"):], "FirehoseStream"
    elif service == "kafka" and resource_part.startswith("cluster/"):
        rid, target_service = resource_part.split("/")[1], "MSKCluster"
    elif service == "glue" and resource_part.startswith("job/"):
        rid, target_service = resource_part[len("job/"):], "GlueJob"
    elif service == "glue" and resource_part.startswith("crawler/"):
        rid, target_service = resource_part[len("crawler/"):], "GlueCrawler"
    elif service == "ssm" and resource_part.startswith("parameter/"):
        # The ARN drops the leading "/" of a path-style name.
        name = resource_part[len("parameter"):]
        rid, target_service = (name if name.count("/") > 1 else name.lstrip("/")), "SSMParameter"
    elif service == "appsync" and resource_part.startswith("apis/"):
        rid, target_service = resource_part[len("apis/"):].split("/")[0], "AppSyncApi"
    elif service == "aoss" and resource_part.startswith("collection/"):
        rid, target_service = resource_part[len("collection/"):], "OpenSearchServerlessCollection"
    elif service == "route53resolver" and resource_part.startswith("resolver-endpoint/"):
        rid, target_service = resource_part[len("resolver-endpoint/"):], "Route53ResolverEndpoint"
    elif service == "sqs":
        # arn:aws:sqs:<region>:<account>:<queue-name> - the resource part is the
        # bare name, with no type prefix like DynamoDB's "table/".
        rid, target_service = resource_part, "SQSQueue"
    elif service == "network-firewall" and resource_part.startswith("firewall/"):
        rid, target_service = resource_part[len("firewall/"):], "NetworkFirewall"
    elif service == "wafv2" and "/webacl/" in resource_part:
        rid, target_service = resource_part.split("/webacl/", 1)[1].split("/")[0], "WAFWebACL"
    elif service == "events" and resource_part.startswith("event-bus/"):
        rid, target_service = resource_part[len("event-bus/"):], "EventBridgeBus"
    elif service == "eks" and resource_part.startswith("cluster/"):
        rid, target_service = resource_part[len("cluster/"):], "EKSCluster"
    elif service == "sagemaker" and resource_part.startswith("endpoint/"):
        # AWS lowercases the name in the ARN; an endpoint named with capitals will not match.
        rid, target_service = resource_part[len("endpoint/"):], "SageMakerEndpoint"
    elif service == "codebuild" and resource_part.startswith("project/"):
        rid, target_service = resource_part[len("project/"):], "CodeBuildProject"
    elif service == "ecs" and resource_part.startswith("service/"):
        # service/<cluster>/<name> is the current format; service/<name> the legacy one.
        rid, target_service = resource_part[len("service/"):], "ECSService"
    elif service == "ecs" and resource_part.startswith("cluster/"):
        rid, target_service = resource_part[len("cluster/"):], "ECSCluster"
    elif service == "states" and resource_part.startswith("stateMachine:"):
        rid, target_service = resource_part[len("stateMachine:"):].split(":")[0], "StepFunctionsStateMachine"
    elif service == "sns":
        # A subscription ARN appends ":<uuid>"; take the topic it belongs to.
        rid, target_service = resource_part.split(":")[0], "SNSTopic"
    elif service == "iam" and resource_part.startswith("role/"):
        # Roles can carry a path; the scanned row is keyed on the final segment.
        rid, target_service = resource_part.split("/")[-1], "IAMRole"
    elif service == "ec2" and "/" in resource_part:
        prefix, _, value = resource_part.partition("/")
        rid, target_service = value, EC2_ARN_PREFIX_TO_SERVICE.get(prefix)
    return rid, target_service


def arn_scope(arn):
    """(account, region) an ARN names, as add_edge scope. A "*" stays a wildcard
    (the grant really covers every region); None means the ARN says nothing."""
    if not arn or not arn.startswith("arn:"):
        return None, None
    parts = arn.split(":", 5)
    if len(parts) < 6:
        return None, None
    region, account = parts[3] or None, parts[4] or None
    return account, region


def probable_resource_id_from_arn(arn):
    """Map an ARN from an IAM policy to (resource_id, internal service), only for
    scanned services; (None, None) rather than a guess that could merge projects."""
    rid, target_service = _resource_id_and_service_from_arn(arn)
    # A wildcard in the name names a SET, handled by resource_id_prefix_from_arn.
    if not rid or "*" in rid or "?" in rid or "${" in rid:
        return None, None
    return rid, target_service


def resource_id_prefix_from_arn(arn):
    """A trailing-wildcard ARN ("table/orders-*") -> (prefix, service). IAM expands it,
    so the grant is real. Interior wildcards and unknown types are refused."""
    rid, target_service = _resource_id_and_service_from_arn(arn)
    if not rid or not target_service or not rid.endswith("*"):
        return None, None
    prefix = rid[:-1]
    if "*" in prefix or "?" in prefix or "${" in prefix:
        return None, None
    if len(prefix) < ARN_PREFIX_MIN_LEN:
        return None, None
    return prefix, target_service


def apigateway_id_from_execute_api_arn(arn):
    """An execute-api ARN -> the API id it grants. No service type: REST and HTTP
    APIs share the ARN shape. "abc123defg/*/*/*" and ".../prod/GET/x" name one API."""
    if not arn or not arn.startswith("arn:"):
        return None
    parts = arn.split(":", 5)
    if len(parts) < 6 or parts[2] != "execute-api":
        return None
    api_id = parts[5].split("/")[0]
    # A wildcard here grants every API in the account; nothing to resolve.
    if not api_id or "*" in api_id or "?" in api_id or "${" in api_id:
        return None
    return api_id
