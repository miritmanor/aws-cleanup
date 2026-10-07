"""AWS resource audit: inventory, activity signals and project grouping.
Usage: README.md. Required read permissions: iam/aws-audit-role.yaml."""

import logging as _logging

# The library never configures logging; this stops logging.lastResort printing
# unformatted WARNINGs in programs that never set logging up.
_logging.getLogger(__name__).addHandler(_logging.NullHandler())

# The public surface, listed explicitly so a moved name fails loudly here.
# Names rebound at runtime are absent: __getattr__ below serves them live.
from .config import (
    ARN_PREFIX_MAX_MATCHES,
    ARN_PREFIX_MIN_LEN,
    CE_SERVICE_MAP,
    COST_LOOKBACK_DAYS,
    CW_LOOKBACK_DAYS,
    GROUP_TAG_KEYS,
    NAME_TOKEN_MAX_SHARE,
    NAME_TOKEN_MIN_LEN,
    NAME_TOKEN_MIN_SUBSTRING,
    NAME_TOKEN_STOPWORDS,
    NON_BRIDGING_SERVICES,
    RETRY_CONFIG,
    SERVICE_BILLING,
    STALE_THRESHOLD_DAYS,
    STRUCTURAL_SERVICES,
    SUPPORTING_SERVICES,
    now,
    set_now,
)
from .errors import AuditError, ScanCancelled
from .region_store import load_active_regions, record_active_regions
# The submodule itself, not names out of it: callers say logsetup.configure().
from . import logsetup
from .analyze.grouping import find_group_signal
from .analyze.name_tokens import build_name_token_index, distinctive_name_tokens, is_all_stopwords, owner_name_tokens, plain_name_tokens, split_name_tokens
from .staleness import days_ago, flag_from_activity, flag_stale
from .text import join_nonempty
from . import activity
from .activity import ActivityEvidence
from .collect.calls import paged, safe_call, tags_to_dict
from .collect.cloudwatch import cw_activity
from .present.format import activity_to_str, display_row, fmt_dt, tags_to_str
from .present.references import render_connections, render_reference
from .connection_types import CONNECTION_TYPES
from .connection_types.vocabulary import (
    ANY_TARGET,
    ARN_RESOLVABLE_TARGETS,
    CONFIDENCE_DISPLAY,
    CONF_AUTHORITATIVE,
    CONF_CONFIG_REFERENCE,
    CONF_HEURISTIC,
    CONN_ALL_STATES,
    CONN_OFF,
    CONN_ON,
    CONN_REPORT_ONLY,
    ConnectionType,
)
from .registry import (
    CONNECTION_TYPES_BY_ID,
    connection_state,
    connection_states,
    default_connection_states,
    detection_enabled,
    expand_connection_patterns,
    feeds_grouping,
    format_connection_type_table,
    resolve_connection_states,
)
from .rows import (
    ROW_FIELDS,
    add_edge,
    resource_key,
    error_row,
    member_key,
    new_row,
)
from .analyze.risk import compute_risk
from .collect.arns import (
    EC2_ARN_PREFIX_TO_SERVICE,
    apigateway_id_from_execute_api_arn,
    probable_resource_id_from_arn,
    resource_id_prefix_from_arn,
)
from .collect.calls import epoch_seconds_to_dt, parse_aws_ts_string
from .collect.cloudwatch import cw_note
from .collect.iam_policy import (
    extract_resource_arns,
    extract_trust_service_principals,
    gather_role_policy_grants,
    parse_policy_document,
    role_name_from_arn,
)
from .collect.regions import collect_default_vpc_ids, get_regions
from .collect.uri_refs import (
    STAGE_VARIABLE_RE,
    apigateway_ids_from_execute_api_urls,
    env_value_resource_candidates,
    infer_log_group_source,
    lambda_name_from_integration_uri,
    resolve_stage_variables,
)
from .collect.resources import (
    collect_eventbridge_rules,
    collect_scheduler_schedules,
    COLLECTORS_PER_REGION,
    EC2_USAGE_COLLECTORS,
    EC2_USAGE_FLOW,
    ROLE_USAGE_COLLECTORS,
    UNUSED_ACCESS_COLLECTORS,
    collect_access_analyzer_unused_roles,
    collect_amis,
    collect_amplify_apps,
    collect_api_gateway_rest_apis,
    collect_api_gateway_v2_apis,
    collect_api_gateway_domain_names,
    collect_appsync_apis,
    collect_auto_scaling_groups,
    collect_backup_plans,
    collect_backup_vaults,
    collect_cloudformation_stacks,
    collect_cloudtrail_trails,
    collect_cloudwatch_alarms,
    collect_classic_load_balancers,
    collect_cloudfront_distributions,
    collect_cloudwatch_log_groups,
    collect_codebuild_projects,
    collect_codepipelines,
    collect_cognito_user_pools,
    collect_dynamodb_tables,
    collect_ebs_snapshots,
    collect_ebs_volumes,
    collect_ec2_instances,
    collect_ecs,
    collect_ecr_repositories,
    collect_kms_keys,
    collect_efs_file_systems,
    collect_eks_clusters,
    collect_elasticache_clusters,
    collect_elastic_beanstalk_applications,
    collect_elastic_beanstalk_environments,
    collect_elastic_ips,
    collect_iam,
    collect_internet_gateways,
    collect_key_pairs,
    collect_lambda_function_env_configs,
    collect_lambda_functions,
    collect_nat_gateways,
    collect_network_firewalls,
    collect_launch_templates,
    collect_load_balancers,
    collect_network_interfaces,
    collect_opensearch_domains,
    collect_opensearch_serverless_collections,
    collect_rds_clusters,
    collect_rds_instances,
    collect_rds_snapshots,
    collect_redshift_clusters,
    collect_redshift_serverless_workgroups,
    collect_reserved_instances,
    collect_route_tables,
    collect_route53_hosted_zones,
    collect_s3_buckets,
    collect_sagemaker_endpoints,
    collect_sagemaker_notebooks,
    collect_sagemaker_studio_apps,
    collect_sagemaker_training_jobs,
    collect_secrets,
    collect_ssm_parameters,
    collect_acm_certificates,
    collect_security_groups,
    collect_sns_topics,
    collect_spot_instance_requests,
    collect_sqs_queues,
    collect_kinesis_streams,
    collect_firehose_streams,
    collect_msk_clusters,
    collect_glue_jobs,
    collect_glue_crawlers,
    collect_state_machines,
    collect_subnets,
    collect_target_groups,
    collect_vpc_endpoints,
    collect_resolver_endpoints,
    collect_transit_gateways,
    collect_vpn_connections,
    collect_vpcs,
    collect_waf_cloudfront_acls,
    collect_waf_web_acls,
    collect_xray_config,
    find_lambda_integrations_rest_api,
    new_ec2_usage_registry,
    validate_collector_order,
)
from .billing import (
    BillingAmount,
    BillingResult,
    CostAttribution,
    CostReport,
    UsageTypeAmount,
    not_queried,
    restore_attribution,
    restore_billing,
)
from .collect.cost import collect_billing
from .analyze.cost import attribute_costs, total_spend
from .analyze.billing_coverage import assess_billing_coverage
from .analyze.project_summary import (
    ProjectOverview,
    ProjectSummary,
    summarize_projects,
)
from .analyze.service_summary import (
    ServiceOverview,
    ServiceSummary,
    summarize_services,
)
from .present.bubbles import (
    METRICS as BUBBLE_METRICS,
    overview_payload,
    pack,
    pack_all,
)
from .present.cost import (
    COST_COLUMN_LABEL,
    cost_columns,
    cost_state_word,
    period_note,
    render_billing_coverage,
    render_unallocated,
)
from .collect.stacks import apply_stack_membership, walk_stack_resources
from .collect.amplify import (
    AMPLIFY_OWNED_RESOURCE_TYPES,
    apply_amplify_api_links,
    find_amplify_backend_resources,
    list_stack_physical_resources,
    normalize_for_name_match,
)
from .analyze.references import resolve_edges
from .analyze.graph import build_graph_data, contract_graph, extract_project
from .analyze.grouping import (
    ASSIGNED,
    AMBIGUOUS,
    SHARED,
    UNASSIGNED,
    apply_project_grouping,
)
from .analyze.tiers import (
    DEFAULT_WHY_TIER,
    TIER_DEPLOYMENT,
    TIER_RUNTIME,
    apply_tiers,
)
from .analyze.group_audit import (
    LARGE_CLUSTER_FRACTION,
    WEAK_EVIDENCE_MARKERS,
    Findings,
    audit_groups,
)
from .present.markdown import (
    render_coverage,
    render_grouping_audit,
    summarize_grouping_audit,
    write_grouping_audit,
)
from .naming.group_names import (
    load_name_rules,
    OVERLAP_THRESHOLD,
    apply_manual_group_overrides,
    apply_assigned_names,
    load_group_store,
    save_group_store,
)
from .settings import (
    CONFIG_FILENAME,
    OUTPUT_FILENAMES,
    RESULTS_DIRNAME,
    RUNTIME_DIRNAME,
    RUNTIME_FILENAMES,
    Settings,
    load_settings,
    output_paths,
)
from .naming.tagging import apply_group_tagging, build_resource_arn, looks_like_arn
from .present.csv import CSV_FIELDNAMES, render_csv, write_csv
from .present.json import render_json, serialize_rows, write_json
from .present.graph.labels import LABEL_BUDGET, mermaid_text, short_label
from .analyze.architecture import ARCHITECTURE_SERVICES, architecture_graph
from .present.graph.mermaid import render_mermaid, write_mermaid
from .present.graph.drawio import render_drawio, write_drawio
from .present.html.template import HTML_TEMPLATE, SERVICE_META
from .present.graph.cytoscape import (
    CYTOSCAPE_VERSION,
    GRAPH_CSS,
    GRAPH_HEAD,
    GRAPH_JS,
    GRAPH_PANEL,
    GRAPH_SWITCH,
)
from .present.html import build_console_url, generate_html_report, render_html_report
from .snapshot import (
    SCHEMA_VERSION,
    SNAPSHOT_ROW_FIELDS,
    Snapshot,
    build_snapshot,
    read_snapshot,
    restore_row,
    snapshot_row,
    write_snapshot,
)
# Must come LAST, as explicit re-exports: `run`/`render` name both a submodule and a
# function, and any later submodule import would rebind the name to the module.
from .render import render, render_outputs
from .run import run


_LIVE = {"CONNECTION_STATES": "registry", "VERBOSE": "console"}


def __getattr__(name):
    """Serve the rebindable globals live from their module, so a re-export
    cannot freeze the value they had at import time."""
    if name in _LIVE:
        import importlib
        return getattr(importlib.import_module(f".{_LIVE[name]}", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    """Include the __getattr__-served names, which dir() cannot see itself."""
    return sorted(set(globals()) | set(_LIVE))
