"""Connection types: CodeBuild/CodePipeline, CloudFormation, Amplify, Elastic Beanstalk."""

from .vocabulary import (
    CONF_AUTHORITATIVE,
    CONF_CONFIG_REFERENCE,
    CONF_HEURISTIC,
    CONN_REPORT_ONLY,
    ConnectionType,
    OWNERSHIP_NONE,
    OWNERSHIP_OWNS,
    OWNERSHIP_SUPPORTING,
    SEM_DEPLOYED_FROM,
    SEM_NAMES,
    SEM_OWNS,
    SEM_READS_OR_WRITES,
    SEM_RUNS_AS,
)

TYPES = (
    # --- CodeBuild / CodePipeline ------------------------------------------
    ConnectionType(
        "codebuild.iamrole.service-role", "CodeBuildProject", ("IAMRole",),
        "project serviceRole", CONF_AUTHORITATIVE,
        "The role a build project runs as.",
        semantics=SEM_RUNS_AS, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "codepipeline.iamrole.role", "CodePipeline", ("IAMRole",),
        "pipeline roleArn", CONF_AUTHORITATIVE,
        "The role a pipeline runs as.",
        semantics=SEM_RUNS_AS, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "codepipeline.s3bucket.artifact-store", "CodePipeline", ("S3Bucket",),
        "pipeline artifactStore.location", CONF_AUTHORITATIVE,
        "The bucket a pipeline passes artifacts through - deployment machinery.",
        semantics=SEM_DEPLOYED_FROM, ownership=OWNERSHIP_OWNS,
),
    ConnectionType(
        "codepipeline.codebuild.action", "CodePipeline", ("CodeBuildProject",),
        "pipeline stage action ProjectName", CONF_AUTHORITATIVE,
        "A build project a pipeline stage runs.",
        semantics=SEM_OWNS, ownership=OWNERSHIP_OWNS,
),
    # --- CloudFormation ----------------------------------------------------
    ConnectionType(
        "cfnstack.any.stack-resource", "CloudFormationStack", ("*",),
        "list_stack_resources physical ids", CONF_AUTHORITATIVE,
        "Every resource a stack and its nested stacks provisioned. AWS's own "
        "record of what a deployment created, which makes it the strongest "
        "answer available to \"whose is this resource\" - a deployment owns "
        "what it deployed, where a policy grant or a shared VPC says nothing "
        "about ownership at all. Generalised out of the Amplify walk, which "
        "reached the same API through one app's backend environment.",
        semantics=SEM_DEPLOYED_FROM, ownership=OWNERSHIP_OWNS,
    ),
    ConnectionType(
        "cfnstack.cfnstack.parent", "CloudFormationStack", ("CloudFormationStack",),
        "stack ParentId (list_stacks)", CONF_AUTHORITATIVE,
        "The parent of a nested stack, named by CloudFormation itself. Tools "
        "that generate a root stack plus a child per feature (Amplify, SAM, CDK) "
        "leave the root looking orphaned without this.",
        semantics=SEM_OWNS, ownership=OWNERSHIP_OWNS,
),
    # --- Amplify -----------------------------------------------------------
    ConnectionType(
        "amplify.apigateway.env-var-execute-api-url", "AmplifyApp",
        ("APIGatewayRestApi", "APIGatewayV2Api"),
        "API id from an execute-api URL in an app or branch environment variable",
        CONF_CONFIG_REFERENCE,
        "Where a front end is told its own API lives - the same mechanism as "
        "lambda.apigateway.env-var-execute-api-url, one level out, and read with "
        "the same extraction. This is usually the ONLY evidence linking an app to "
        "an API it did not provision: the stack walk sees what Amplify deployed, "
        "and an API added by hand appears in no stack. Read from the app's "
        "environmentVariables and each branch's, since branches routinely point "
        "at different stages of the same API. The extracted id is not "
        "type-namespaced, so it cannot be guarded against a same-id collision.",
        semantics=SEM_READS_OR_WRITES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "amplify.any.cfn-stack-walk", "AmplifyApp",
        ("APIGatewayRestApi", "APIGatewayV2Api", "LambdaFunction", "DynamoDBTable",
         "S3Bucket", "CognitoUserPool", "CloudFormationStack"),
        "backend environment CloudFormation stack resources", CONF_AUTHORITATIVE,
        "Everything an Amplify Gen 1 backend actually provisioned, by walking the "
        "app's root stack and every nested stack. AWS's own deployment record.",
        semantics=SEM_DEPLOYED_FROM, ownership=OWNERSHIP_OWNS,
),
    ConnectionType(
        "amplify.cfnstack.root-stack", "AmplifyApp", ("CloudFormationStack",),
        "list_backend_environments stackName", CONF_AUTHORITATIVE,
        "The root CloudFormation stack of an Amplify backend environment, matched by "
        "name because ListBackendEnvironments returns no ARN.",
        semantics=SEM_DEPLOYED_FROM, ownership=OWNERSHIP_OWNS,
),
    ConnectionType(
        "amplify.apigateway.name-match", "AmplifyApp",
        ("APIGatewayRestApi", "APIGatewayV2Api"),
        "Amplify app name resembles an API name", CONF_HEURISTIC,
        "Last-resort fallback used only when no backend environment exists at all "
        "(Hosting-only apps, or Gen 2 apps under a stack layout this doesn't walk). "
        "Pure name resemblance - reported by default, but never trusted to put resources in a project.",
        default_state=CONN_REPORT_ONLY,
        semantics=SEM_NAMES, ownership=OWNERSHIP_NONE,
),
    # --- Elastic Beanstalk --------------------------------------------------
    ConnectionType(
        "elasticbeanstalk.ec2instance.environment-resource", "ElasticBeanstalkEnvironment",
        ("EC2Instance",), "describe_environment_resources Instances", CONF_AUTHORITATIVE,
        "The EC2 instances an environment is running behind the scenes - what "
        "makes an instance with no application tags of its own explainable "
        "rather than a floating orphan.",
        semantics=SEM_DEPLOYED_FROM, ownership=OWNERSHIP_OWNS,
),
    ConnectionType(
        "elasticbeanstalk.loadbalancer.environment-resource", "ElasticBeanstalkEnvironment",
        ("LoadBalancer",), "describe_environment_resources LoadBalancers", CONF_AUTHORITATIVE,
        "The load balancer an environment provisioned in front of its "
        "instances. A classic ELB (pre-ALB environments) resolves to nothing, "
        "since this script only scans ELBv2 - reported as dangling rather "
        "than silently dropped.",
        semantics=SEM_DEPLOYED_FROM, ownership=OWNERSHIP_OWNS,
),
    ConnectionType(
        "elasticbeanstalk.s3bucket.deployment-artifact", "ElasticBeanstalkEnvironment",
        ("S3Bucket",), "describe_application_versions SourceBundle.S3Bucket", CONF_AUTHORITATIVE,
        "The S3 bucket holding the currently-deployed application version's "
        "source bundle, read off AWS's own version record for the "
        "environment's VersionLabel - the same authoritative shape as "
        "Amplify's deploymentArtifacts, one service over.",
        semantics=SEM_READS_OR_WRITES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "elasticbeanstalk.ebapplication.application-name", "ElasticBeanstalkEnvironment",
        ("ElasticBeanstalkApplication",), "describe_environments ApplicationName",
        CONF_AUTHORITATIVE,
        "The application an environment deploys. Recorded from the environment "
        "because that is the side AWS states it on; its value is the other "
        "direction, where an application with no inbound edge is one whose "
        "environments are all gone and whose versions and bucket folder are not.",
        semantics=SEM_OWNS, ownership=OWNERSHIP_OWNS,
),
    # --- Elastic Beanstalk application -------------------------------------
    ConnectionType(
        "ebapplication.s3bucket.source-bundle", "ElasticBeanstalkApplication",
        ("S3Bucket",), "describe_application_versions SourceBundle.S3Bucket",
        CONF_AUTHORITATIVE,
        "Buckets named by this application's version source bundles. A bundle "
        "may sit in a bucket outside the account - AWS's own sample "
        "application is the usual case - so these never report DANGLING.",
        semantics=SEM_READS_OR_WRITES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "ebapplication.s3bucket.service-bucket", "ElasticBeanstalkApplication",
        ("S3Bucket",), "application folder present in elasticbeanstalk-<region>-<account>",
        CONF_CONFIG_REFERENCE,
        "The account's Elastic Beanstalk service bucket, named by a format AWS "
        "defines and then CONFIRMED by probing for this application's own "
        "folder inside it. Not a name-match heuristic: the prefix is built "
        "from a name AWS gave us and the probe can only confirm or refute it, "
        "so nothing is ever inferred from object names someone chose. This is "
        "the only mechanism that reaches the bucket of an application that "
        "was never deployed to an environment.",
        semantics=SEM_READS_OR_WRITES, ownership=OWNERSHIP_SUPPORTING,
),
    ConnectionType(
        "lambda.iamrole.execution-role", "LambdaFunction", ("IAMRole",),
        "function configuration Role", CONF_AUTHORITATIVE,
        "The role a function assumes when it runs. Replaces the old "
        "group.shared-iam-role attribute union, which recorded the same fact "
        "but also merged every project sharing one execution role - a "
        "lambda-basic-execution role is shared by everything precisely "
        "because it grants nothing specific.",
        semantics=SEM_RUNS_AS, ownership=OWNERSHIP_SUPPORTING,
        target_scope="account",
    ),
    ConnectionType(
        "ec2.iamrole.instance-profile", "EC2Instance", ("IAMRole",),
        "instance profile role", CONF_AUTHORITATIVE,
        "The role an instance assumes through its instance profile.",
        semantics=SEM_RUNS_AS, ownership=OWNERSHIP_SUPPORTING,
        target_scope="account",
    ),
)
