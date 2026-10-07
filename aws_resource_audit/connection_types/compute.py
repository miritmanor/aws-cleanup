"""Connection types: EC2, Auto Scaling, launch templates, ECS, EKS, Lambda, Step Functions, SageMaker."""

from .vocabulary import (
    ARN_RESOLVABLE_TARGETS,
    CONF_AUTHORITATIVE,
    CONF_CONFIG_REFERENCE,
    CONF_HEURISTIC,
    CONN_REPORT_ONLY,
    ConnectionType,
    OWNERSHIP_NONE,
    OWNERSHIP_OWNS,
    OWNERSHIP_SUPPORTING,
    SEM_INVOKES,
    SEM_NAMES,
    SEM_OWNS,
    SEM_READS_OR_WRITES,
    SEM_REFERENCES,
    SEM_RUNS_AS,
    SEM_SHARES_NETWORK,
    SEM_USES_IMAGE,
)

TYPES = (
    # --- EC2 instance ------------------------------------------------------
    ConnectionType(
        "ec2.ami.image-id", "EC2Instance", ("AMI",),
        "instance ImageId", CONF_AUTHORITATIVE,
        "The AMI an instance was launched from, read off the instance's own ImageId.",
        semantics=SEM_USES_IMAGE, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "ec2.keypair.key-name", "EC2Instance", ("KeyPair",),
        "instance KeyName", CONF_AUTHORITATIVE,
        "The key pair an instance was launched with. KeyName is a user-chosen "
        "string, so this is type-guarded against same-name collisions.",
        semantics=SEM_REFERENCES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "ec2.securitygroup.membership", "EC2Instance", ("SecurityGroup",),
        "instance SecurityGroups", CONF_AUTHORITATIVE,
        "Security groups attached to an instance.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    # --- Auto Scaling groups ----------------------------------------------
    ConnectionType(
        "asg.launchtemplate.reference", "AutoScalingGroup", ("LaunchTemplate",),
        "group LaunchTemplate", CONF_AUTHORITATIVE,
        "The launch template a group launches instances from - what keeps it in use.",
        semantics=SEM_REFERENCES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "asg.ec2.instance-membership", "AutoScalingGroup", ("EC2Instance",),
        "group Instances", CONF_AUTHORITATIVE,
        "An instance the group launched and manages. Containment: terminate the "
        "group, not the instance, or it is replaced.",
        semantics=SEM_OWNS, ownership=OWNERSHIP_OWNS,
),
    ConnectionType(
        "asg.subnet.placement", "AutoScalingGroup", ("Subnet",),
        "group VPCZoneIdentifier", CONF_AUTHORITATIVE,
        "A subnet the group launches instances into.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "asg.targetgroup.attachment", "AutoScalingGroup", ("TargetGroup",),
        "group TargetGroupARNs", CONF_AUTHORITATIVE,
        "A target group the group registers its instances with.",
        semantics=SEM_REFERENCES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "targetgroup.loadbalancer.attachment", "TargetGroup", ("LoadBalancer",),
        "target group LoadBalancerArns", CONF_AUTHORITATIVE,
        "The load balancer that forwards to a target group.",
        semantics=SEM_REFERENCES, ownership=OWNERSHIP_SUPPORTING,
),
    # --- Launch templates / spot -------------------------------------------
    ConnectionType(
        "launchtemplate.ami.image-id", "LaunchTemplate", ("AMI",),
        "launch template default version ImageId", CONF_AUTHORITATIVE,
        "The AMI a launch template's default version would launch.",
        semantics=SEM_USES_IMAGE, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "launchtemplate.keypair.key-name", "LaunchTemplate", ("KeyPair",),
        "launch template default version KeyName", CONF_AUTHORITATIVE,
        "The key pair a launch template's default version would use.",
        semantics=SEM_REFERENCES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "launchtemplate.securitygroup.membership", "LaunchTemplate", ("SecurityGroup",),
        "launch template default version security groups", CONF_AUTHORITATIVE,
        "Security groups a launch template's default version would attach, "
        "including those nested under its NetworkInterfaces.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "spotrequest.ec2.instance-id", "SpotInstanceRequest", ("EC2Instance",),
        "spot request InstanceId", CONF_AUTHORITATIVE,
        "The instance a spot request launched.",
        semantics=SEM_OWNS, ownership=OWNERSHIP_OWNS,
),
    # --- ECS --------------------------------------------------------------
    ConnectionType(
        "ecscluster.ec2instance.container-instance", "ECSCluster", ("EC2Instance",),
        "DescribeContainerInstances ec2InstanceId", CONF_AUTHORITATIVE,
        "An EC2 instance registered with the cluster to run its tasks. Containment: "
        "the instance exists to give the cluster capacity.",
        semantics=SEM_OWNS, ownership=OWNERSHIP_OWNS,
),
    ConnectionType(
        "ecsservice.ecscluster.membership", "ECSService", ("ECSCluster",),
        "service clusterArn", CONF_AUTHORITATIVE,
        "The cluster a service runs in. Containment, so the two share a project.",
        semantics=SEM_OWNS, ownership=OWNERSHIP_OWNS,
),
    ConnectionType(
        "ecsservice.subnet.placement", "ECSService", ("Subnet",),
        "service awsvpcConfiguration.subnets", CONF_AUTHORITATIVE,
        "A subnet a service's tasks run in.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "ecsservice.securitygroup.membership", "ECSService", ("SecurityGroup",),
        "service awsvpcConfiguration.securityGroups", CONF_AUTHORITATIVE,
        "A security group on a service's tasks.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "ecsservice.targetgroup.attachment", "ECSService", ("TargetGroup",),
        "service loadBalancers.targetGroupArn", CONF_AUTHORITATIVE,
        "A target group a service registers its tasks with.",
        semantics=SEM_REFERENCES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "ecsservice.loadbalancer.target", "ECSService", ("LoadBalancer",),
        "target group LoadBalancerArns", CONF_AUTHORITATIVE,
        "The load balancer that routes to a service, through its target group. Fargate IP targets name no resource, so this is the only line from the balancer.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "ecsservice.iamrole.task-role", "ECSService", ("IAMRole",),
        "task definition taskRoleArn", CONF_AUTHORITATIVE,
        "The role a service's containers run as.",
        semantics=SEM_RUNS_AS, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "lambda.ecrrepository.image", "LambdaFunction", ("ECRRepository",),
        "GetFunction Code.ImageUri", CONF_AUTHORITATIVE,
        "The ECR repository a container-image function runs from.",
        semantics=SEM_USES_IMAGE, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "ecsservice.ecrrepository.image", "ECSService", ("ECRRepository",),
        "task definition image", CONF_AUTHORITATIVE,
        "An ECR repository a service's containers run images from.",
        semantics=SEM_USES_IMAGE, ownership=OWNERSHIP_SUPPORTING,
),
    # --- EKS --------------------------------------------------------------
    ConnectionType(
        "ekscluster.subnet.placement", "EKSCluster", ("Subnet",),
        "cluster resourcesVpcConfig.subnetIds", CONF_AUTHORITATIVE,
        "A subnet the cluster's control plane network interfaces are in.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "ekscluster.securitygroup.membership", "EKSCluster", ("SecurityGroup",),
        "cluster resourcesVpcConfig", CONF_AUTHORITATIVE,
        "A security group on the cluster.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "ekscluster.iamrole.cluster-role", "EKSCluster", ("IAMRole",),
        "cluster roleArn", CONF_AUTHORITATIVE,
        "The role the control plane uses to manage AWS resources.",
        semantics=SEM_RUNS_AS, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "ekscluster.autoscalinggroup.nodegroup", "EKSCluster", ("AutoScalingGroup",),
        "nodegroup resources.autoScalingGroups", CONF_AUTHORITATIVE,
        "The Auto Scaling group behind one of the cluster's managed node groups. Containment.",
        semantics=SEM_OWNS, ownership=OWNERSHIP_OWNS,
),
    # --- Lambda: env-var strategies are split because their reliability differs sharply.
    ConnectionType(
        "lambda.any.env-var-arn-value", "LambdaFunction", ARN_RESOLVABLE_TARGETS,
        "env var value IS an ARN", CONF_CONFIG_REFERENCE,
        "An environment variable whose entire value is an ARN naming a scanned resource.",
        semantics=SEM_READS_OR_WRITES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "lambda.any.env-var-arn-embedded", "LambdaFunction", ARN_RESOLVABLE_TARGETS,
        "ARN embedded in env var value", CONF_CONFIG_REFERENCE,
        "An ARN found inside a larger env var value (JSON blob, URL, connection string).",
        semantics=SEM_READS_OR_WRITES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "lambda.apigateway.env-var-execute-api-url", "LambdaFunction",
        ("APIGatewayRestApi", "APIGatewayV2Api"),
        "API id from an execute-api URL in an env var", CONF_CONFIG_REFERENCE,
        "How an app usually learns its own API endpoint. The extracted id is not "
        "type-namespaced, so it cannot be guarded against a same-id collision.",
        semantics=SEM_READS_OR_WRITES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "lambda.any.env-var-bare-name", "LambdaFunction", ARN_RESOLVABLE_TARGETS,
        "env var value matches a scanned resource name exactly", CONF_HEURISTIC,
        "An env var holding a bare token that exactly equals some scanned resource's "
        "name. The weakest signal here: env vars hold plenty of non-resource strings, "
        "and the value carries no type information to guard the match. Reported by "
        "default but not trusted to put resources in a project.",
        default_state=CONN_REPORT_ONLY,
        # Account-wide: an env var can name a table in any region; a name found in
        # two regions resolves to neither and is reported ambiguous.
        target_scope="account",
        semantics=SEM_NAMES, ownership=OWNERSHIP_NONE,
),
    ConnectionType(
        "lambda.any.dead-letter-config", "LambdaFunction", ARN_RESOLVABLE_TARGETS,
        "Lambda DeadLetterConfig.TargetArn", CONF_AUTHORITATIVE,
        "The SQS queue or SNS topic a function sends failed invocations to.",
        semantics=SEM_READS_OR_WRITES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "lambda.any.event-source-mapping", "LambdaFunction",
        ("SQSQueue", "DynamoDBTable", "KinesisStream", "MSKCluster"),
        "list_event_source_mappings EventSourceArn", CONF_AUTHORITATIVE,
        "The queues and streams AWS invokes this function from. The missing "
        "half of a queue's story: without it an SQS queue feeding a Lambda "
        "looks unconnected, and an idle one looks abandoned rather than "
        "merely quiet. A DISABLED mapping is still recorded - someone wired "
        "these together, and that it is currently switched off is evidence "
        "about the deployment, not a reason to hide the link.",
        semantics=SEM_READS_OR_WRITES, ownership=OWNERSHIP_SUPPORTING,
    ),
    ConnectionType(
        "lambda.loggroup.logging-config", "LambdaFunction", ("CloudWatchLogGroup",),
        "Lambda LoggingConfig.LogGroup", CONF_AUTHORITATIVE,
        "An explicitly configured (non-default) log group for a function.",
        semantics=SEM_OWNS, ownership=OWNERSHIP_OWNS,
),
    # --- Step Functions ----------------------------------------------------
    ConnectionType(
        "statemachine.any.definition-reference", "StepFunctionsStateMachine", ARN_RESOLVABLE_TARGETS,
        "state machine definition ARNs", CONF_AUTHORITATIVE,
        "A resource the state machine's definition names by ARN - what it invokes or writes to.",
        semantics=SEM_INVOKES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "statemachine.dynamodb.table-parameter", "StepFunctionsStateMachine", ("DynamoDBTable",),
        "DynamoDB task Parameters.TableName", CONF_AUTHORITATIVE,
        "A table a DynamoDB task names. The task's Resource says it is a table name, so this is no guess.",
        semantics=SEM_READS_OR_WRITES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "statemachine.s3bucket.bucket-parameter", "StepFunctionsStateMachine", ("S3Bucket",),
        "S3 task Parameters.Bucket", CONF_AUTHORITATIVE,
        "A bucket an S3 SDK task names. The task's Resource says it is a bucket name, so this is no guess.",
        semantics=SEM_READS_OR_WRITES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "statemachine.iamrole.execution-role", "StepFunctionsStateMachine", ("IAMRole",),
        "state machine roleArn", CONF_AUTHORITATIVE,
        "The role a state machine assumes when it runs.",
        semantics=SEM_RUNS_AS, ownership=OWNERSHIP_SUPPORTING,
),
    # --- SageMaker ---------------------------------------------------------
    ConnectionType(
        "sagemakernotebook.subnet.placement", "SageMakerNotebook", ("Subnet",),
        "notebook SubnetId", CONF_AUTHORITATIVE,
        "The subnet a VPC-attached notebook instance runs in.",
        semantics=SEM_SHARES_NETWORK, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "sagemakernotebook.iamrole.role", "SageMakerNotebook", ("IAMRole",),
        "notebook RoleArn", CONF_AUTHORITATIVE,
        "The role a notebook instance runs as.",
        semantics=SEM_RUNS_AS, ownership=OWNERSHIP_SUPPORTING,
),
)
