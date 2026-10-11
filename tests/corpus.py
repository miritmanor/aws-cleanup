"""A deterministic synthetic account for the golden renders: every service, plus the
awkward cases (report-only, dangling, collision, shared hub, bridge, tiers, billing)."""

import contextlib
import io
from datetime import timedelta
from decimal import Decimal

from .fakes import ancient, days_before_now, make_row, recent

import aws_resource_audit as audit


# The canonical service list; test_golden.py checks it against the collector coverage guard.
ALL_SERVICES = [
    "EC2Instance", "EBSVolume", "EBSSnapshot", "AMI", "ReservedInstance", "ElasticIP",
    "NetworkInterface", "SecurityGroup", "KeyPair", "LaunchTemplate", "SpotInstanceRequest",
    "LoadBalancer", "RDSInstance", "DynamoDBTable", "S3Bucket", "CloudWatchLogGroup",
    "LambdaFunction", "APIGatewayRestApi", "APIGatewayV2Api", "CloudWatchAlarm",
    "CloudTrailTrail", "AmplifyApp", "CognitoUserPool", "XRayGroup", "XRaySamplingRule",
    "CloudFormationStack", "IAMUser", "IAMRole", "IAMGroup", "IAMPolicy",
    "IAMRoleUnusedAccessFinding", "SQSQueue", "SNSTopic",
    "ElasticBeanstalkEnvironment", "ElasticBeanstalkApplication",
    "EventBridgeRule", "EventBridgeSchedule",
    "VPC", "Subnet", "RouteTable", "InternetGateway", "NatGateway", "VpcEndpoint",
    "RDSCluster", "RDSSnapshot", "EFSFileSystem", "ElastiCacheCluster", "OpenSearchDomain",
    "KMSKey", "Secret", "Route53HostedZone", "CloudFrontDistribution", "ECRRepository",
    "AutoScalingGroup", "TargetGroup", "StepFunctionsStateMachine", "ECSCluster", "ECSService",
    "CodeBuildProject", "CodePipeline", "BackupVault", "BackupPlan",
    "DocumentDBCluster", "DocumentDBInstance", "NeptuneCluster", "NeptuneInstance",
    "SageMakerEndpoint", "SageMakerNotebook", "EKSCluster", "RedshiftCluster", "EventBridgeBus",
    "WAFWebACL", "NetworkFirewall", "APIGatewayDomainName",
    "RedshiftServerlessWorkgroup", "SageMakerStudioApp", "SageMakerTrainingJob",
    "KinesisStream", "FirehoseStream", "MSKCluster", "GlueJob", "GlueCrawler",
    "TransitGateway", "VPNConnection", "ACMCertificate", "SSMParameter", "AppSyncApi",
    "OpenSearchServerlessCollection", "Route53ResolverEndpoint",
]

# One representative id per service. Names are deliberately unalike, so no shared word
# fuses the corpus into one cluster.
_BASELINE = {
    "EC2Instance": ("i-0baseline0000001", "jenkins"),
    "EBSVolume": ("vol-0baseline000001", "jenkins-root"),
    "EBSSnapshot": ("snap-0baseline00001", "weekly-backup"),
    "AMI": ("ami-0baseline000001", "hardened-base"),
    "ReservedInstance": ("ri-0baseline000001", "reserved-capacity"),
    "ElasticIP": ("eipalloc-0base0001", "egress-address"),
    "NetworkInterface": ("eni-0baseline00001", "jenkins-primary"),
    "SecurityGroup": ("sg-0baseline000001", "web-ingress"),
    "KeyPair": ("key-0baseline00001", "deploy-key"),
    "LaunchTemplate": ("lt-0baseline000001", "worker-template"),
    "SpotInstanceRequest": ("sir-0baseline0001", "batch-capacity"),
    "LoadBalancer": ("alb-frontdoor", "frontdoor"),
    "RDSInstance": ("billing-postgres", "billing-postgres"),
    "DynamoDBTable": ("sessions", "sessions"),
    "S3Bucket": ("static-assets-archive", "static-assets-archive"),
    "CloudWatchLogGroup": ("/aws/lambda/scheduler", "scheduler-logs"),
    "LambdaFunction": ("scheduler", "scheduler"),
    "APIGatewayRestApi": ("restapi0001", "public-rest"),
    "APIGatewayV2Api": ("httpapi0001", "public-http"),
    "CloudWatchAlarm": ("disk-space-critical", "disk-space-critical"),
    "CloudTrailTrail": ("management-events", "management-events"),
    "AmplifyApp": ("amplify0001", "marketing-site"),
    "CognitoUserPool": ("us-east-1_pool0001", "customer-directory"),
    "XRayGroup": ("xraygroup0001", "latency-watch"),
    "XRaySamplingRule": ("xraysampling0001", "default-sampling"),
    "CloudFormationStack": ("arn:aws:cloudformation:us-east-1:111122223333:stack/infra/0001", "infra"),
    "IAMUser": ("deploybot", "deploybot"),
    "IAMRole": ("ReadOnlyAuditor", "ReadOnlyAuditor"),
    "IAMGroup": ("Engineering", "Engineering"),
    "IAMPolicy": ("arn:aws:iam::111122223333:policy/BudgetGuard", "BudgetGuard"),
    "IAMRoleUnusedAccessFinding": ("finding0001", "unused-role-finding"),
    "SQSQueue": ("invoice-intake", "invoice-intake"),
    "SNSTopic": ("arn:aws:sns:us-east-1:111122223333:pager-alerts", "pager-alerts"),
    "ElasticBeanstalkEnvironment": ("e-baseline0000001", "legacy-app-env"),
    # Unalike "legacy-app-env" on purpose; the real app-to-environment link is tested elsewhere.
    "ElasticBeanstalkApplication": ("ledger-platform", "ledger-platform"),
    "EventBridgeRule": ("nightly-report", "nightly-report"),
    "EventBridgeSchedule": ("weekly-cleanup", "weekly-cleanup"),
    "VPC": ("vpc-0baseline000001", "office-network"),
    "Subnet": ("subnet-0baseline0001", "dmz-a"),
    "RouteTable": ("rtb-0baseline000001", "hub-routing"),
    "InternetGateway": ("igw-0baseline000001", "front-gate"),
    "NatGateway": ("nat-0baseline000001", "outbound-nat"),
    "VpcEndpoint": ("vpce-0baseline00001", "private-link"),
    "RDSCluster": ("warehouse-db", "warehouse-db"),
    "RDSSnapshot": ("quarterly-dump", "quarterly-dump"),
    "EFSFileSystem": ("fs-0baseline000001", "shared-home"),
    "ElastiCacheCluster": ("redis-0001", "hot-keys"),
    "OpenSearchDomain": ("catalogue-search", "catalogue-search"),
    "OpenSearchServerlessCollection": ("aoss0baseline01", "vector-recall"),
    "KMSKey": ("1234abcd-12ab-34cd-56ef-1234567890ab", "alias/payroll"),
    "Secret": ("smtp-credentials", "smtp-credentials"),
    "ACMCertificate": ("0baseline-cert-0001", "wombat.test"),
    "SSMParameter": ("/feature/flags", "/feature/flags"),
    "AppSyncApi": ("gql0baseline01", "menu-graphql"),
    "Route53HostedZone": ("Z0BASELINE00001", "example.org"),
    "Route53ResolverEndpoint": ("rslvr-in-0baseline", "datacenter-dns"),
    "CloudFrontDistribution": ("E0BASELINE00001", "d111abcdef8.cloudfront.net"),
    "ECRRepository": ("container-builds", "container-builds"),
    "AutoScalingGroup": ("render-farm", "render-farm"),
    "TargetGroup": ("blue-pool", "blue-pool"),
    "StepFunctionsStateMachine": ("refund-flow", "refund-flow"),
    "ECSCluster": ("fargate-main", "fargate-main"),
    "ECSService": ("fargate-main/checkout-svc", "checkout-svc"),
    "CodeBuildProject": ("unit-tests", "unit-tests"),
    "CodePipeline": ("release-train", "release-train"),
    "BackupVault": ("vault-primary", "vault-primary"),
    "BackupPlan": ("plan-0001", "daily-retention"),
    "DocumentDBCluster": ("docdb-cluster-0001", "reviews"),
    "DocumentDBInstance": ("docdb-instance-0001", "paperwork"),
    "NeptuneCluster": ("neptune-cluster-0001", "friendships"),
    "NeptuneInstance": ("neptune-instance-0001", "lineage"),
    "SageMakerEndpoint": ("churn-predictor", "churn-predictor"),
    "SageMakerNotebook": ("experiments", "experiments"),
    "SageMakerStudioApp": ("d-1/alice/JupyterLab/default", "alice JupyterLab"),
    "SageMakerTrainingJob": ("forecast-fit-0007", "forecast-fit-0007"),
    "EKSCluster": ("kube-prod", "kube-prod"),
    "RedshiftCluster": ("redshift-0001", "dashboards-dw"),
    "RedshiftServerlessWorkgroup": ("adhoc-olap", "adhoc-olap"),
    "KinesisStream": ("clickstream-raw", "clickstream-raw"),
    "FirehoseStream": ("eventsink-firehose", "eventsink-firehose"),
    "MSKCluster": ("orderbus-msk", "orderbus-msk"),
    "GlueJob": ("dedupe-rollup", "dedupe-rollup"),
    "GlueCrawler": ("schema-sniffer", "schema-sniffer"),
    "TransitGateway": ("tgw-0baseline00001", "branch-interconnect"),
    "VPNConnection": ("vpn-0baseline00001", "onprem-tunnel"),
    "EventBridgeBus": ("partner-events", "partner-events"),
    "WAFWebACL": ("edge-shield", "edge-shield"),
    "NetworkFirewall": ("perimeter-fw", "perimeter-fw"),
    "APIGatewayDomainName": ("hooks.widgetco.test", "hooks.widgetco.test"),
}

# Global services get "global" as their region, exactly as the collectors emit.
_GLOBAL = {"IAMUser", "IAMRole", "IAMGroup", "IAMPolicy",
           "IAMRoleUnusedAccessFinding", "Route53HostedZone", "CloudFrontDistribution"}


def quiet(fn, *args, **kwargs):
    """Run a pass that prints a progress summary, discarding stdout."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def _baseline_rows():
    """One row per service, alternating active/stale so both branches of the
    flag and risk columns appear in the goldens."""
    rows = []
    for index, service in enumerate(ALL_SERVICES):
        resource_id, name = _BASELINE[service]
        stale = index % 3 == 0
        rows.append(make_row(
            service, resource_id, name,
            region="global" if service in _GLOBAL else "us-east-1",
            created=ancient(1200) if stale else days_before_now(200),
            last_used=ancient(900) if stale else recent(4),
            last_used_days=900 if stale else 4,
            inferred=stale,
            flag="STALE (INFERRED)" if stale else "ACTIVE",
            notes="Baseline corpus row.",
        ))
    return rows


def _linked_rows():
    """The part of the corpus that actually exercises the output layer."""
    rows = []

    # --- A real project: API -> Lambda -> table, plus its log group. ---------
    api = make_row("APIGatewayRestApi", "restapi9001", "orders-api",
                   tags={"Project": "orders"})
    fn = make_row("LambdaFunction", "orders-worker", "orders-worker",
                  tags={"Project": "orders"}, iam_role="OrdersExecution")
    table = make_row("DynamoDBTable", "orders-live", "orders-live",
                     tags={"Project": "orders"})
    logs = make_row("CloudWatchLogGroup", "/aws/lambda/orders-worker",
                    "orders-worker-logs")

    audit.add_edge(api, "orders-worker", "integrates", "REST API integration URI",
                   conn_type="apigateway.lambda.integration",
                   target_service="LambdaFunction")
    audit.add_edge(fn, "/aws/lambda/orders-worker", "logs to",
                   "Lambda LoggingConfig", conn_type="lambda.loggroup.logging-config",
                   target_service="CloudWatchLogGroup")
    # Report-only: drawn and described from both ends, never allowed to group.
    audit.add_edge(fn, "orders-live", "may reference",
                   "environment variable TABLE=orders-live",
                   conn_type="lambda.any.env-var-bare-name",
                   target_service="DynamoDBTable", assert_exists=False)
    # Dangling: named outright by AWS config, absent from the scan.
    audit.add_edge(fn, "orders-archive", "writes to",
                   "environment variable ARCHIVE_TABLE",
                   conn_type="lambda.any.env-var-arn-value",
                   target_service="DynamoDBTable")
    rows += [api, fn, table, logs]

    # --- An IAM role reaching a set of tables by prefix grant. --------------
    role = make_row("IAMRole", "OrdersExecution", "OrdersExecution", region="global")
    audit.add_edge(role, "orders-", "grants access to",
                   "inline policy Resource table/orders-*",
                   conn_type="iamrole.any.resource-grant-prefix",
                   target_service="DynamoDBTable", target_match="prefix")
    # A grant with no direct link behind it, so the role's own edges are not
    # merely a restatement of something the columns already say.
    audit.add_edge(role, "static-assets-archive", "grants access to",
                   "inline policy Resource arn:aws:s3:::static-assets-archive/*",
                   conn_type="iamrole.any.resource-grant",
                   target_service="S3Bucket")
    # The only edge pointing AT an API. It reaches the row outputs but not the drawn graph:
    # a role-to-pool pair has no structural end to bridge onto.
    audit.add_edge(role, "restapi9001", "IAM policy grants API access to",
                   "execute-api Resource ARN 'arn:aws:execute-api:us-east-1:"
                   "123456789012:restapi9001/*/*/*' on this role",
                   conn_type="iamrole.apigateway.execute-api-grant")
    rows.append(role)

    # The bridge: app -> user pool (hidden) -> Lambda contracts into one bridged edge whose
    # endpoints' connections columns never mention each other.
    portal = make_row("AmplifyApp", "amplify9001", "orders-portal",
                      tags={"Project": "orders"})
    pool = make_row("CognitoUserPool", "us-east-1_orders01", "orders-directory")
    audit.add_edge(portal, "us-east-1_orders01", "provisions",
                   "backend environment CloudFormation stack resources",
                   conn_type="amplify.any.cfn-stack-walk",
                   target_service="CognitoUserPool")
    audit.add_edge(pool, "orders-worker", "triggers",
                   "user pool LambdaConfig",
                   conn_type="cognito.lambda.trigger",
                   target_service="LambdaFunction")
    # The two-hop chain that must NOT bridge.
    audit.add_edge(pool, "OrdersExecution", "SMS caller role",
                   "user pool SmsConfiguration.SnsCallerArn",
                   conn_type="cognito.iamrole.sms-caller",
                   target_service="IAMRole")
    # The runtime/deployment split: one project holding deployment, runtime and
    # unclassified rows, through the real classification path.
    # In another region than the app, as buckets often are: its links must still resolve.
    deploy_bucket = make_row("S3Bucket", "orders-deploy-artifacts",
                             "orders-deploy-artifacts", region="us-west-2",
                             tags={"Project": "orders"})
    # A custom resource run once during `amplify push`: deployment, not an idle Lambda.
    push_fn = make_row("LambdaFunction", "orders-userpool-client",
                       "orders-userpool-client", tags={"Project": "orders"})
    portal["_amplify_deployment_artifacts"] = ["orders-deploy-artifacts"]
    portal["_amplify_provenance"] = {
        "orders-userpool-client": {"logical_id": "UserPoolClientLambda",
                                   "category": "auth",
                                   "type": "AWS::Lambda::Function"},
        # In a category stack, so the same app's own function is runtime.
        "orders-live": {"logical_id": "OrdersTable", "category": "storage",
                        "type": "AWS::DynamoDB::Table"},
    }
    rows += [portal, pool, deploy_bucket, push_fn]

    # --- A collision: the id matches, the type does not. Must be refused. ---
    collide = make_row("EC2Instance", "i-0collision000001", "reporting-box")
    audit.add_edge(collide, "static-assets-archive", "launched from",
                   "instance ImageId", conn_type="ec2.ami.image-id",
                   target_service="AMI")
    rows.append(collide)

    # --- A second, unrelated project sharing a VPC and a tag. ---------------
    web = make_row("EC2Instance", "i-0webtier00000001", "webtier",
                   tags={"Project": "storefront"}, vpc_id="vpc-0shared000001")
    cache = make_row("RDSInstance", "storefront-cache", "storefront-cache",
                     tags={"Project": "storefront"}, vpc_id="vpc-0shared000001")
    rows += [web, cache]

    # --- The hub. Wired to both projects; must not fuse them. ---------------
    hub = make_row("SecurityGroup", "sg-0sharedhub00001", "ssh-from-anywhere")
    audit.add_edge(web, "sg-0sharedhub00001", "member of", "instance SecurityGroups",
                   conn_type="ec2.securitygroup.membership",
                   target_service="SecurityGroup")
    audit.add_edge(collide, "sg-0sharedhub00001", "member of",
                   "instance SecurityGroups",
                   conn_type="ec2.securitygroup.membership",
                   target_service="SecurityGroup")
    rows.append(hub)

    # --- A costed row. -----------------------------------------------------
    costed = make_row("EC2Instance", "i-0expensive000001", "gpu-trainer",
                      flag="STALE", inferred=False, last_used_days=800,
                      created=ancient(1000), last_used=ancient(800))
    rows.append(costed)

    # No error row: failed lookups never reach the rendering side.
    return rows


def coverage_entries():
    """A coverage ledger: EC2 instances complete in us-east-1 (divisible) and DynamoDB
    tables denied there (not divisible). Everything else absent, i.e. unknown."""
    stamp = audit.now().isoformat()
    return [
        {"account": "123456789012", "scope": "us-east-1", "collector": "EC2 instances",
         "service": service, "capability": "inventory", "operation": "EC2 instances",
         "status": "complete", "scanned_at": stamp, "error": "", "count": 1}
        for service in ("EC2Instance", "ReservedInstance", "SpotInstanceRequest")
    ] + [
        {"account": "123456789012", "scope": "us-east-1", "collector": "DynamoDB tables",
         "service": "DynamoDBTable", "capability": "inventory",
         "operation": "DynamoDB tables", "status": "denied", "scanned_at": stamp,
         "error": "AccessDenied: dynamodb:ListTables", "count": 0},
    ]


def billing():
    """A bill with one of each situation the cost pass has to tell apart."""
    end = audit.now().date()
    return audit.BillingResult(
        amounts=(
            # Divisible: no declared gaps, and every type billing into it was
            # enumerated completely in this region.
            audit.BillingAmount("Amazon Elastic Compute Cloud - Compute",
                                "us-east-1", Decimal("825.10")),
            # Not divisible: the same bill covers NAT gateways and VPC
            # endpoints, which nothing here collects.
            audit.BillingAmount("EC2 - Other", "us-east-1", Decimal("40.00")),
            # Not divisible: the collector was denied, so the rows found are
            # not all the rows the charge could belong to.
            audit.BillingAmount("Amazon DynamoDB", "us-east-1", Decimal("3.00")),
            # Billed, and nothing in this script has ever looked at it.
            audit.BillingAmount("Amazon Lex", "us-east-1",
                                Decimal("12.34")),
            # Not a resource type, and not a coverage finding.
            audit.BillingAmount("Tax", "", Decimal("5.00")),
        ),
        usage_types=(
            audit.UsageTypeAmount("EC2 - Other", "USE1-NatGateway-Hours",
                                  Decimal("31.00")),
            audit.UsageTypeAmount("EC2 - Other", "USE1-EBS:VolumeUsage.gp3",
                                  Decimal("9.00")),
        ),
        period_start=end - timedelta(days=audit.COST_LOOKBACK_DAYS),
        period_end=end, currency="USD", account="123456789012",
        estimated=False, complete=True, queried=True, observed_at=audit.now())


def build():
    """The finished account through the real passes in run()'s order. Returns (rows,
    contracted_graph, full_graph, grouping_notes)."""
    rows = _baseline_rows() + _linked_rows()
    edge_links = audit.resolve_edges(rows)
    # After resolve_edges (the runtime rule reads resolved incoming links) and
    # independent of grouping in both directions - the same order run() uses.
    audit.apply_tiers(rows)
    notes = quiet(audit.apply_project_grouping, rows, edge_links=edge_links,
                   default_vpc_ids={"vpc-0default00001"})
    full = audit.build_graph_data(rows, notes.get("grouping_links"))
    contracted = audit.contract_graph(full)
    # Last, as in run(): it reads membership, so it cannot run before grouping.
    audit.attribute_costs(rows, billing(), coverage_entries=coverage_entries())
    return rows, contracted, full, notes
