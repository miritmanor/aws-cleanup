"""Connection types: SQS/SNS, EventBridge rules and buses, API Gateway."""

from .vocabulary import (
    CONF_AUTHORITATIVE,
    ConnectionType,
    OWNERSHIP_OWNS,
    OWNERSHIP_SUPPORTING,
    SEM_INVOKES,
    SEM_READS_OR_WRITES,
    SEM_RUNS_AS,
    SEM_SHARES_NETWORK,
)

TYPES = (
    # --- AppSync -----------------------------------------------------------
    ConnectionType(
        "appsync.lambda.datasource", "AppSyncApi", ("LambdaFunction",),
        "data source lambdaConfig.lambdaFunctionArn", CONF_AUTHORITATIVE,
        "A Lambda function an AppSync API resolves fields with.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "appsync.dynamodb.datasource", "AppSyncApi", ("DynamoDBTable",),
        "data source dynamodbConfig.tableName", CONF_AUTHORITATIVE,
        "A DynamoDB table an AppSync API reads and writes directly.",
        semantics=SEM_READS_OR_WRITES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "appsync.cognito.authorizer", "AppSyncApi", ("CognitoUserPool",),
        "userPoolConfig.userPoolId", CONF_AUTHORITATIVE,
        "A Cognito user pool whose tokens an AppSync API accepts.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "appsync.lambda.authorizer", "AppSyncApi", ("LambdaFunction",),
        "lambdaAuthorizerConfig.authorizerUri", CONF_AUTHORITATIVE,
        "A Lambda function an AppSync API calls to authorize a request.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    # --- Kinesis / Firehose ------------------------------------------------
    ConnectionType(
        "firehose.kinesis.source", "FirehoseStream", ("KinesisStream",),
        "Source KinesisStreamARN", CONF_AUTHORITATIVE,
        "The Kinesis stream a delivery stream reads its records from.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "firehose.s3bucket.destination", "FirehoseStream", ("S3Bucket",),
        "destination BucketARN", CONF_AUTHORITATIVE,
        "The bucket a delivery stream writes its records to.",
        semantics=SEM_READS_OR_WRITES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "firehose.lambda.processor", "FirehoseStream", ("LambdaFunction",),
        "ProcessingConfiguration LambdaArn", CONF_AUTHORITATIVE,
        "A Lambda function that transforms each batch before delivery.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "firehose.iamrole.role", "FirehoseStream", ("IAMRole",),
        "destination RoleARN", CONF_AUTHORITATIVE,
        "The role a delivery stream assumes to write to its destination.",
        semantics=SEM_RUNS_AS, ownership=OWNERSHIP_SUPPORTING,
),
    # --- MSK ---------------------------------------------------------------
    ConnectionType(
        "msk.subnet.placement", "MSKCluster", ("Subnet",),
        "BrokerNodeGroupInfo.ClientSubnets / VpcConfigs.SubnetIds", CONF_AUTHORITATIVE,
        "A subnet holding one of the cluster's brokers - one per availability zone.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "msk.securitygroup.membership", "MSKCluster", ("SecurityGroup",),
        "BrokerNodeGroupInfo.SecurityGroups / VpcConfigs.SecurityGroupIds", CONF_AUTHORITATIVE,
        "A security group on the cluster's brokers.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    # --- Messaging ---------------------------------------------------------
    ConnectionType(
        "s3bucket.any.notification", "S3Bucket",
        ("LambdaFunction", "SQSQueue", "SNSTopic"),
        "bucket notification configuration", CONF_AUTHORITATIVE,
        "What a bucket triggers when an object lands in it. Without this a "
        "bucket that fires a Lambda on upload reads as an unconnected pile of "
        "storage - the worst kind of false cleanup candidate, because "
        "deleting it breaks a pipeline that leaves no other trace. Bucket "
        "objects are never read; this is the bucket's own configuration.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
    ),
    # --- EventBridge -------------------------------------------------------
    ConnectionType(
        "eventbridgerule.any.target", "EventBridgeRule",
        ("LambdaFunction", "SQSQueue", "SNSTopic", "StepFunctionsStateMachine"),
        "list_targets_by_rule Arn", CONF_AUTHORITATIVE,
        "What a rule runs when it matches. This is often the ONLY caller a "
        "resource has: a Lambda invoked by a nightly rule is referenced by "
        "nothing else in the account, so without this it reads as "
        "unreferenced while running every night.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
    ),
    ConnectionType(
        "eventbridgerule.iamrole.target-role", "EventBridgeRule", ("IAMRole",),
        "rule target RoleArn", CONF_AUTHORITATIVE,
        "The role EventBridge assumes to invoke the target.",
        semantics=SEM_RUNS_AS, ownership=OWNERSHIP_SUPPORTING,
        target_scope="account",
    ),
    ConnectionType(
        "eventbridgeschedule.any.target", "EventBridgeSchedule",
        ("LambdaFunction", "SQSQueue", "SNSTopic", "StepFunctionsStateMachine"),
        "GetSchedule Target Arn", CONF_AUTHORITATIVE,
        "What a schedule runs, and where it dead-letters. Same reasoning as "
        "the rule target: for a scheduled job this is the whole of its "
        "inbound wiring.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
    ),
    ConnectionType(
        "eventbridgeschedule.iamrole.target-role", "EventBridgeSchedule",
        ("IAMRole",),
        "schedule Target RoleArn", CONF_AUTHORITATIVE,
        "The role EventBridge Scheduler assumes to invoke the target.",
        semantics=SEM_RUNS_AS, ownership=OWNERSHIP_SUPPORTING,
        target_scope="account",
    ),
    ConnectionType(
        "sqs.sqs.redrive-policy", "SQSQueue", ("SQSQueue",),
        "queue RedrivePolicy deadLetterTargetArn", CONF_AUTHORITATIVE,
        "The dead-letter queue a source queue diverts repeatedly-failed messages "
        "to. Read off the source queue's own redrive policy, so the DLQ never "
        "looks orphaned just because nothing publishes to it directly.",
        semantics=SEM_READS_OR_WRITES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "sns.any.subscription", "SNSTopic", ("LambdaFunction", "SQSQueue"),
        "list_subscriptions_by_topic Endpoint", CONF_AUTHORITATIVE,
        "What a topic actually fans out to. Only Lambda and SQS endpoints match a "
        "scanned row; email/SMS/HTTP subscriptions are shown as text since there "
        "is no AWS resource on the other end to link to.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    # --- EventBridge buses -------------------------------------------------
    ConnectionType(
        "eventbridgerule.eventbridgebus.membership", "EventBridgeRule", ("EventBridgeBus",),
        "rule EventBusName", CONF_AUTHORITATIVE,
        "The custom bus a rule matches events on. The bus routes events through the "
        "rule, and the rule belongs to it.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_OWNS,
),
    # --- API Gateway -------------------------------------------------------
    ConnectionType(
        "apigateway.cognito.authorizer", "APIGatewayRestApi", ("CognitoUserPool",),
        "GetAuthorizers providerARNs", CONF_AUTHORITATIVE,
        "A Cognito user pool whose tokens a REST API's authorizer accepts.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "apigateway.lambda.authorizer", "APIGatewayRestApi", ("LambdaFunction",),
        "GetAuthorizers authorizerUri", CONF_AUTHORITATIVE,
        "A Lambda function a REST API calls to decide whether a request may proceed.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "apigatewayv2.cognito.authorizer", "APIGatewayV2Api", ("CognitoUserPool",),
        "GetAuthorizers JwtConfiguration.Issuer", CONF_AUTHORITATIVE,
        "A Cognito user pool named as the issuer of an HTTP API's JWT authorizer.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "apigatewayv2.lambda.authorizer", "APIGatewayV2Api", ("LambdaFunction",),
        "GetAuthorizers AuthorizerUri", CONF_AUTHORITATIVE,
        "A Lambda function an HTTP or WebSocket API calls to authorize a request.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "apidomain.apigateway.mapping", "APIGatewayDomainName", ("APIGatewayRestApi",),
        "GetBasePathMappings restApiId", CONF_AUTHORITATIVE,
        "A REST API a custom domain name sends requests to, under one base path.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "apidomain.apigatewayv2.mapping", "APIGatewayDomainName", ("APIGatewayV2Api",),
        "GetApiMappings ApiId", CONF_AUTHORITATIVE,
        "An HTTP or WebSocket API a custom domain name sends requests to.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "apigateway.lambda.integration", "APIGatewayRestApi", ("LambdaFunction",),
        "REST API method integration URI (get_integration)", CONF_AUTHORITATIVE,
        "The Lambda a REST API method routes to - AWS's own routing config.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "apigateway.lambda.stage-variable", "APIGatewayRestApi", ("LambdaFunction",),
        "integration URI ${stageVariables.*} resolved from stage config", CONF_AUTHORITATIVE,
        "The Lambda a REST API routes to when the integration URI names it "
        "indirectly, via a ${stageVariables.X} placeholder each stage fills in. "
        "Both halves come from AWS config and API Gateway itself performs the "
        "substitution, so this is as authoritative as a literal URI - without it "
        "the API appears to invoke a function called '${stageVariables.X}' and is "
        "reported as dangling.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "apigatewayv2.lambda.integration", "APIGatewayV2Api", ("LambdaFunction",),
        "HTTP/WebSocket API IntegrationUri (get_integrations)", CONF_AUTHORITATIVE,
        "The Lambda an HTTP or WebSocket API routes to.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
)
