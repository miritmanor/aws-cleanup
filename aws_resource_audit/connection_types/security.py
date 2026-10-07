"""Connection types: IAM, Cognito, Secrets Manager, CloudTrail and CloudWatch Logs."""

from .vocabulary import (
    ARN_RESOLVABLE_TARGETS,
    CONF_AUTHORITATIVE,
    ConnectionType,
    OWNERSHIP_NONE,
    OWNERSHIP_OWNS,
    OWNERSHIP_SUPPORTING,
    SEM_INVOKES,
    SEM_OWNS,
    SEM_PERMITS_ACCESS_TO,
    SEM_READS_OR_WRITES,
    SEM_REFERENCES,
    SEM_RUNS_AS,
)

TYPES = (
    # --- IAM ---------------------------------------------------------------
    ConnectionType(
        "iampolicy.iamrole.attachment", "IAMPolicy", ("IAMRole",),
        "list_entities_for_policy", CONF_AUTHORITATIVE,
        "Roles a customer-managed policy is attached to.",
        semantics=SEM_PERMITS_ACCESS_TO, ownership=OWNERSHIP_NONE,
),
    ConnectionType(
        "iampolicy.iamuser.attachment", "IAMPolicy", ("IAMUser",),
        "list_entities_for_policy", CONF_AUTHORITATIVE,
        "Users a customer-managed policy is attached to.",
        semantics=SEM_PERMITS_ACCESS_TO, ownership=OWNERSHIP_NONE,
),
    ConnectionType(
        "iampolicy.iamgroup.attachment", "IAMPolicy", ("IAMGroup",),
        "list_entities_for_policy", CONF_AUTHORITATIVE,
        "Groups a customer-managed policy is attached to.",
        semantics=SEM_PERMITS_ACCESS_TO, ownership=OWNERSHIP_NONE,
),
    ConnectionType(
        "iampolicy.any.resource-grant", "IAMPolicy", ARN_RESOLVABLE_TARGETS,
        "Resource ARN in the policy document", CONF_AUTHORITATIVE,
        "Resources a policy document grants access to, from its Resource elements.",
        semantics=SEM_PERMITS_ACCESS_TO, ownership=OWNERSHIP_NONE,
),
    ConnectionType(
        "iamrole.any.resource-grant", "IAMRole", ARN_RESOLVABLE_TARGETS,
        "Resource ARN in the role's own policies", CONF_AUTHORITATIVE,
        "Resources a role can touch, from its inline and customer-managed policies "
        "(AWS-managed ones are skipped as too wildcarded to mean anything specific). "
        "Often the only way to recover a Lambda->table link when the table name is "
        "built at runtime in application code.",
        semantics=SEM_PERMITS_ACCESS_TO, ownership=OWNERSHIP_NONE,
),
    ConnectionType(
        "iampolicy.any.resource-grant-prefix", "IAMPolicy", ARN_RESOLVABLE_TARGETS,
        "prefix-wildcard Resource ARN in the policy document", CONF_AUTHORITATIVE,
        "Same as iampolicy.any.resource-grant, for a Resource whose name ends in a "
        "wildcard (\"table/orders*\"): every scanned resource of that type whose id "
        "starts with the prefix. IAM expands the pattern itself, so the grant is a "
        "fact rather than a name-match guess - see resource_id_prefix_from_arn for "
        "the limits that keep a short prefix from claiming the whole account.",
        semantics=SEM_PERMITS_ACCESS_TO, ownership=OWNERSHIP_NONE,
),
    ConnectionType(
        "iamrole.any.resource-grant-prefix", "IAMRole", ARN_RESOLVABLE_TARGETS,
        "prefix-wildcard Resource ARN in the role's own policies", CONF_AUTHORITATIVE,
        "Same as iamrole.any.resource-grant, for a Resource whose name ends in a "
        "wildcard. Without it a role that names one table exactly and its dev/test "
        "siblings by prefix - the ordinary shape of a hand-written policy - links "
        "only to the one, and the graph shows a function reaching some of its tables "
        "and not the rest for no visible reason.",
        semantics=SEM_PERMITS_ACCESS_TO, ownership=OWNERSHIP_NONE,
),
    ConnectionType(
        "iampolicy.apigateway.execute-api-grant", "IAMPolicy",
        ("APIGatewayRestApi", "APIGatewayV2Api"),
        "execute-api Resource ARN in the policy document", CONF_AUTHORITATIVE,
        "The API a policy lets its holder call. Split from "
        "iampolicy.any.resource-grant because an execute-api ARN is the one grant "
        "whose id cannot carry a target service - REST and HTTP APIs share an ARN "
        "shape - so it is resolved without a type guard and deserves to be "
        "switchable on its own.",
        semantics=SEM_PERMITS_ACCESS_TO, ownership=OWNERSHIP_NONE,
),
    ConnectionType(
        "iamrole.apigateway.execute-api-grant", "IAMRole",
        ("APIGatewayRestApi", "APIGatewayV2Api"),
        "execute-api Resource ARN in the role's own policies", CONF_AUTHORITATIVE,
        "The API a role is allowed to call. This is how an IAM-authorized front end "
        "reaches its backend: the Cognito authenticated-user role carries "
        "execute-api:Invoke on the API, and without this type nothing in the account "
        "points AT an API Gateway at all - every API looks like a caller and never a "
        "callee, and the front door of an application is invisible.",
        semantics=SEM_PERMITS_ACCESS_TO, ownership=OWNERSHIP_NONE,
),
    # --- Cognito -----------------------------------------------------------
    ConnectionType(
        "cognito.lambda.trigger", "CognitoUserPool", ("LambdaFunction",),
        "describe_user_pool LambdaConfig", CONF_AUTHORITATIVE,
        "A Lambda a user pool invokes as part of its auth flow (pre-signup, "
        "custom message, define/create auth challenge, ...). These functions "
        "implement the pool's behaviour, so they belong with it - but the ARNs "
        "are only on describe_user_pool, not the list_user_pools summary.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "cognito.iamrole.sms-caller", "CognitoUserPool", ("IAMRole",),
        "describe_user_pool SmsConfiguration.SnsCallerArn", CONF_AUTHORITATIVE,
        "The IAM role a user pool assumes to publish SMS via SNS.",
        semantics=SEM_RUNS_AS, ownership=OWNERSHIP_SUPPORTING,
),
    # --- ACM ---------------------------------------------------------------
    ConnectionType(
        "acmcertificate.any.in-use", "ACMCertificate",
        ("LoadBalancer", "CloudFrontDistribution", "APIGatewayDomainName"),
        "DescribeCertificate InUseBy", CONF_AUTHORITATIVE,
        "A load balancer, CloudFront distribution or API domain serving this certificate.",
        semantics=SEM_REFERENCES, ownership=OWNERSHIP_SUPPORTING,
),
    # --- SSM Parameter Store -----------------------------------------------
    ConnectionType(
        "ssmparameter.kmskey.encryption", "SSMParameter", ("KMSKey",),
        "parameter KeyId", CONF_AUTHORITATIVE,
        "The KMS key a SecureString parameter is encrypted with.",
        semantics=SEM_REFERENCES, ownership=OWNERSHIP_SUPPORTING,
),
    # --- Secrets Manager ---------------------------------------------------
    ConnectionType(
        "secret.kmskey.encryption", "Secret", ("KMSKey",),
        "secret KmsKeyId", CONF_AUTHORITATIVE,
        "The customer-managed key a secret is encrypted with; deleting the key "
        "makes the secret unreadable.",
        semantics=SEM_REFERENCES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "secret.lambda.rotation", "Secret", ("LambdaFunction",),
        "secret RotationLambdaARN", CONF_AUTHORITATIVE,
        "The function that rotates a secret on a schedule.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    # --- CloudTrail / CloudWatch Logs --------------------------------------
    ConnectionType(
        "loadbalancer.any.target", "LoadBalancer",
        ("EC2Instance", "LambdaFunction", "LoadBalancer"),
        "target group registered targets", CONF_AUTHORITATIVE,
        "What the load balancer routes to, through its target groups. The "
        "collector used to stop at the load balancer, so an instance behind "
        "an ALB looked unconnected and the ALB looked like it served nothing. "
        "Target health travels as evidence and nothing more: an unhealthy "
        "target is a resource with a problem, not an abandoned one.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
    ),
    ConnectionType(
        "cloudtrail.loggroup.arn", "CloudTrailTrail", ("CloudWatchLogGroup",),
        "DescribeTrails CloudWatchLogsLogGroupArn", CONF_AUTHORITATIVE,
        "The log group a trail also delivers to, when configured beyond its S3 destination.",
        semantics=SEM_READS_OR_WRITES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "loggroup.any.name-convention", "CloudWatchLogGroup",
        ("LambdaFunction", "APIGatewayRestApi", "RDSInstance", "RDSCluster", "AmplifyApp"),
        "AWS-enforced log group naming", CONF_AUTHORITATIVE,
        "What writes to a log group, inferred from naming AWS itself enforces and "
        "auto-creates (/aws/lambda/NAME and friends). Naming that is merely a common "
        "convention (CodeBuild, ECS, EKS, Step Functions) is deliberately excluded - "
        "it is shown as text but never becomes an edge.",
        semantics=SEM_OWNS, ownership=OWNERSHIP_OWNS,
),
    ConnectionType(
        "loggroup.any.subscription-filter", "CloudWatchLogGroup", ARN_RESOLVABLE_TARGETS,
        "subscription filter destinationArn", CONF_AUTHORITATIVE,
        "Where a log group forwards events to (Lambda, Kinesis, Firehose, OpenSearch).",
        semantics=SEM_READS_OR_WRITES, ownership=OWNERSHIP_SUPPORTING,
),
)
