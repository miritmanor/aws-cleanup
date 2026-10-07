"""Per-service collectors: one function per AWS resource type, returning new_row() rows,
or an error_row() when the lookup failed. New AWS coverage goes here."""

from .ec2 import (
    collect_ebs_volumes,
    collect_ec2_instances,
    collect_elastic_ips,
    collect_reserved_instances,
    collect_spot_instance_requests,
)
from .ec2_access import collect_key_pairs, collect_network_interfaces, collect_security_groups
from .ec2_images import collect_amis, collect_ebs_snapshots, collect_launch_templates
from .ec2_usage import new_ec2_usage_registry
from .data import collect_dynamodb_tables, collect_rds_instances, collect_s3_buckets
from .compute import collect_lambda_function_env_configs, collect_lambda_functions
from .edge import (
    collect_api_gateway_rest_apis,
    collect_classic_load_balancers,
    collect_api_gateway_v2_apis,
    collect_api_gateway_domain_names,
    collect_load_balancers,
    find_lambda_integrations_rest_api,
)
from .events import collect_eventbridge_rules, collect_scheduler_schedules
from .datastores import (
    collect_elasticache_clusters,
    collect_opensearch_domains,
    collect_opensearch_serverless_collections,
)
from .autoscaling import collect_auto_scaling_groups
from .backup import collect_backup_plans, collect_backup_vaults
from .sagemaker import (
    collect_sagemaker_endpoints,
    collect_sagemaker_notebooks,
    collect_sagemaker_studio_apps,
    collect_sagemaker_training_jobs,
)
from .stepfunctions import collect_state_machines
from .targetgroups import collect_target_groups
from .waf import collect_waf_cloudfront_acls, collect_waf_web_acls
from .networkfirewall import collect_network_firewalls
from .cicd import collect_codebuild_projects, collect_codepipelines
from .cloudfront import collect_cloudfront_distributions
from .dns import collect_resolver_endpoints, collect_route53_hosted_zones
from .ecr import collect_ecr_repositories
from .ecs import collect_ecs
from .efs import collect_efs_file_systems
from .eks import collect_eks_clusters
from .keys import collect_kms_keys, collect_secrets, collect_ssm_parameters
from .certificates import collect_acm_certificates
from .rds import collect_rds_clusters, collect_rds_snapshots
from .redshift import collect_redshift_clusters, collect_redshift_serverless_workgroups
from .network import (
    collect_internet_gateways,
    collect_nat_gateways,
    collect_route_tables,
    collect_subnets,
    collect_vpc_endpoints,
    collect_vpcs,
)
from .messaging import collect_sns_topics, collect_sqs_queues
from .glue import collect_glue_crawlers, collect_glue_jobs
from .appsync import collect_appsync_apis
from .transit import collect_transit_gateways, collect_vpn_connections
from .streaming import collect_firehose_streams, collect_kinesis_streams, collect_msk_clusters
from .observability import (
    collect_cloudtrail_trails,
    collect_cloudwatch_alarms,
    collect_cloudwatch_log_groups,
    collect_xray_config,
)
from .deployment import (
    collect_amplify_apps,
    collect_cloudformation_stacks,
    collect_elastic_beanstalk_applications,
    collect_elastic_beanstalk_environments,
)
from .identity import (
    collect_access_analyzer_unused_roles,
    collect_cognito_user_pools,
    collect_iam,
)


# ORDER IS LOAD-BEARING for EC2: producers (instances, ENIs, launch templates, AMIs) must
# run before consumers, or used resources are flagged unused. EC2_USAGE_FLOW checks this.
COLLECTORS_PER_REGION = [
    ("EC2 instances", collect_ec2_instances),
    ("Network interfaces", collect_network_interfaces),
    ("VPCs", collect_vpcs),
    ("Subnets", collect_subnets),
    ("Route tables", collect_route_tables),
    ("Internet gateways", collect_internet_gateways),
    ("NAT gateways", collect_nat_gateways),
    ("VPC endpoints", collect_vpc_endpoints),
    ("Route 53 Resolver endpoints", collect_resolver_endpoints),
    ("Transit gateways", collect_transit_gateways),
    ("VPN connections", collect_vpn_connections),
    ("Auto Scaling groups", collect_auto_scaling_groups),
    ("Launch templates", collect_launch_templates),
    ("AMIs (self-owned)", collect_amis),
    ("EBS snapshots", collect_ebs_snapshots),
    ("Security groups", collect_security_groups),
    ("Key pairs", collect_key_pairs),
    ("Reserved instances", collect_reserved_instances),
    ("Spot requests", collect_spot_instance_requests),
    ("EBS volumes", collect_ebs_volumes),
    ("Elastic IPs", collect_elastic_ips),
    ("RDS instances", collect_rds_instances),
    ("RDS clusters", collect_rds_clusters),
    ("RDS snapshots", collect_rds_snapshots),
    ("Lambda functions", collect_lambda_functions),
    ("Load balancers", collect_load_balancers),
    ("Classic load balancers", collect_classic_load_balancers),
    ("Target groups", collect_target_groups),
    ("ECS clusters and services", collect_ecs),
    ("EKS clusters", collect_eks_clusters),
    ("Redshift clusters", collect_redshift_clusters),
    ("Redshift Serverless workgroups", collect_redshift_serverless_workgroups),
    ("WAF web ACLs", collect_waf_web_acls),
    ("Network Firewall firewalls", collect_network_firewalls),
    ("CodeBuild projects", collect_codebuild_projects),
    ("CodePipeline pipelines", collect_codepipelines),
    ("Backup vaults", collect_backup_vaults),
    ("Backup plans", collect_backup_plans),
    ("SageMaker endpoints", collect_sagemaker_endpoints),
    ("SageMaker notebook instances", collect_sagemaker_notebooks),
    ("SageMaker Studio apps", collect_sagemaker_studio_apps),
    ("SageMaker training jobs", collect_sagemaker_training_jobs),
    ("DynamoDB tables", collect_dynamodb_tables),
    ("EFS file systems", collect_efs_file_systems),
    ("ElastiCache clusters", collect_elasticache_clusters),
    ("OpenSearch domains", collect_opensearch_domains),
    ("OpenSearch Serverless collections", collect_opensearch_serverless_collections),
    ("KMS keys", collect_kms_keys),
    ("Secrets Manager secrets", collect_secrets),
    ("SSM advanced parameters", collect_ssm_parameters),
    ("ACM certificates", collect_acm_certificates),
    ("ECR repositories", collect_ecr_repositories),
    ("SQS queues", collect_sqs_queues),
    ("Kinesis streams", collect_kinesis_streams),
    ("Firehose delivery streams", collect_firehose_streams),
    ("MSK clusters", collect_msk_clusters),
    ("Glue jobs", collect_glue_jobs),
    ("Glue crawlers", collect_glue_crawlers),
    ("SNS topics", collect_sns_topics),
    ("EventBridge rules", collect_eventbridge_rules),
    ("EventBridge schedules", collect_scheduler_schedules),
    ("Step Functions state machines", collect_state_machines),
    ("API Gateway REST APIs", collect_api_gateway_rest_apis),
    ("API Gateway HTTP/WebSocket APIs", collect_api_gateway_v2_apis),
    ("API Gateway custom domains", collect_api_gateway_domain_names),
    ("AppSync GraphQL APIs", collect_appsync_apis),
    ("CloudTrail trails", collect_cloudtrail_trails),
    ("CloudWatch log groups", collect_cloudwatch_log_groups),
    ("CloudWatch alarms", collect_cloudwatch_alarms),
    ("Amplify apps", collect_amplify_apps),
    ("X-Ray groups/sampling rules", collect_xray_config),
    ("CloudFormation stacks", collect_cloudformation_stacks),
    ("Elastic Beanstalk environments", collect_elastic_beanstalk_environments),
    ("Elastic Beanstalk applications", collect_elastic_beanstalk_applications),
    ("Cognito user pools", collect_cognito_user_pools),
    ("IAM unused-role findings", collect_access_analyzer_unused_roles),
]

# Account-wide services with no extra arguments, run once after every region.
# S3 and IAM are also global but take registries, so collect.py calls them itself.
COLLECTORS_GLOBAL = [
    ("Route 53 hosted zones", collect_route53_hosted_zones),
    ("CloudFront distributions", collect_cloudfront_distributions),
    ("WAF web ACLs (CloudFront)", collect_waf_cloudfront_acls),
]

# Collectors that also need to record which IAM roles they saw in active use,
# so the IAM role report can cross-reference against real, scanned consumers.
ROLE_USAGE_COLLECTORS = {collect_ec2_instances, collect_lambda_functions}
UNUSED_ACCESS_COLLECTORS = {collect_access_analyzer_unused_roles}
# Collectors that read from and/or write to the shared EC2 reference registry.
EC2_USAGE_COLLECTORS = {
    collect_ec2_instances, collect_network_interfaces, collect_launch_templates,
    collect_amis, collect_ebs_snapshots, collect_security_groups,
    collect_key_pairs, collect_reserved_instances, collect_vpcs, collect_subnets,
    collect_auto_scaling_groups,
}

# Which collectors fill each shared-registry key and which read it, checked at import
# by validate_collector_order(): a wrong order fails silently otherwise.
EC2_USAGE_FLOW = {
    "image_ids": {
        "producers": {collect_ec2_instances, collect_launch_templates},
        "consumers": {collect_amis},
    },
    "key_names": {
        "producers": {collect_ec2_instances, collect_launch_templates},
        "consumers": {collect_key_pairs},
    },
    "sg_ids": {
        "producers": {collect_ec2_instances, collect_launch_templates,
                      collect_network_interfaces},
        "consumers": {collect_security_groups},
    },
    "snapshot_ids": {
        # AMIs are both a consumer (of image_ids) and a producer (of the
        # snapshot ids backing them), which is why they sit mid-list.
        "producers": {collect_amis},
        "consumers": {collect_ebs_snapshots},
    },
    "active_ami_snapshot_ids": {
        # Snapshots backing a non-stale AMI, so a snapshot can tell used from forgotten.
        "producers": {collect_amis},
        "consumers": {collect_ebs_snapshots},
    },
    "instance_types": {
        "producers": {collect_ec2_instances},
        "consumers": {collect_reserved_instances},
    },
    # A VPC or subnet is in use when a network interface is in it.
    "vpc_ids": {
        "producers": {collect_network_interfaces},
        "consumers": {collect_vpcs},
    },
    "subnet_ids": {
        "producers": {collect_network_interfaces},
        "consumers": {collect_subnets},
    },
    "launch_template_ids": {
        "producers": {collect_auto_scaling_groups},
        "consumers": {collect_launch_templates},
    },
}


def validate_collector_order(collectors=None):
    """Raise if a shared-registry consumer is scheduled before its producer.
    Called at import."""
    order = {fn: i for i, (_label, fn) in enumerate(collectors or COLLECTORS_PER_REGION)}
    problems = []
    for key, flow in sorted(EC2_USAGE_FLOW.items()):
        producers = [fn for fn in flow["producers"] if fn in order]
        consumers = [fn for fn in flow["consumers"] if fn in order]
        if not producers or not consumers:
            continue  # one side isn't scheduled at all; nothing to order
        first_consumer = min(order[fn] for fn in consumers)
        late = sorted(fn.__name__ for fn in producers if order[fn] > first_consumer)
        if late:
            early = sorted(fn.__name__ for fn in consumers if order[fn] == first_consumer)
            problems.append(f"{key}: {', '.join(early)} runs before {', '.join(late)}")
    if problems:
        raise RuntimeError(
            "COLLECTORS_PER_REGION is mis-ordered - a consumer of the shared EC2 usage "
            "registry runs before its producer, which silently reports in-use resources "
            "as unused:\n  " + "\n  ".join(problems))
    return True


validate_collector_order()
