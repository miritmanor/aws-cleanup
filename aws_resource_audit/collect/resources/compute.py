"""Lambda functions, and the environment variables of $LATEST and every aliased version."""

import logging

from botocore.exceptions import BotoCoreError, ClientError

from ... import console
from ..arns import arn_scope, probable_resource_id_from_arn
from ..calls import paged, parse_aws_ts_string
from ..cloudwatch import cw_activity, activity_note
from ..iam_policy import role_name_from_arn
from ..uri_refs import ecr_repository_from_image, env_value_resource_candidates, lambda_qualifier_from_arn
from ... import coverage
from ...config import RETRY_CONFIG
from ...staleness import days_ago, flag_from_activity
from ...collect.calls import safe_call
from ...collect import raw_capture
from ...text import join_nonempty
from ...rows import add_edge, error_row, new_row
from ...console import say

logger = logging.getLogger(__name__)


def collect_lambda_function_env_configs(lam, function_name):
    """[(qualifier, env vars)] for $LATEST plus each version an alias points at, since
    each published version has its own environment. ([], error) if aliases fail."""
    configs = []
    alias_versions = {}   # version -> [alias labels]

    aliases = []
    try:
        for page in lam.get_paginator("list_aliases").paginate(FunctionName=function_name):
            aliases.extend(page.get("Aliases", []))
    except (ClientError, BotoCoreError) as e:
        logger.warning("listing aliases for lambda %s failed: %s",
                       function_name, e)
        return [], str(e)

    for alias in aliases:
        ver = alias.get("FunctionVersion")
        if ver:
            alias_versions.setdefault(ver, []).append(alias["Name"])
        # A weighted alias splits traffic across two versions - both are live.
        for wver in (alias.get("RoutingConfig", {}) or {}).get("AdditionalVersionWeights", {}):
            alias_versions.setdefault(wver, []).append(f"{alias['Name']} (weighted)")

    for ver, alias_names in sorted(alias_versions.items()):
        if ver == "$LATEST":
            continue  # already covered by the list_functions payload
        detail = safe_call(lam.get_function_configuration, FunctionName=function_name, Qualifier=ver)
        if "__error__" in detail:
            continue
        env = (detail.get("Environment", {}) or {}).get("Variables", {}) or {}
        configs.append((f"alias {'/'.join(alias_names)} -> version {ver}", env))
    return configs, None


def _image_repository(lam, fn):
    """The ECR repository a container-image function runs from. list_functions
    says which functions are images; only those cost the extra GetFunction call."""
    if fn.get("PackageType") != "Image":
        return None
    resp = safe_call(lam.get_function, FunctionName=fn["FunctionName"])
    code = {} if "__error__" in resp else resp.get("Code", {})
    return ecr_repository_from_image(code.get("ImageUri") or code.get("ResolvedImageUri"))


def collect_lambda_functions(session, region, role_usage=None):
    lam = session.client("lambda", region_name=region, config=RETRY_CONFIG)
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    paginator = lam.get_paginator("list_functions")
    try:
        for page in paginator.paginate():
            for fn in page["Functions"]:
                name = fn["FunctionName"]
                role_name = None
                if fn.get("Role"):
                    role_name = role_name_from_arn(fn.get("Role"))
                    if role_usage is not None and role_name:
                        role_usage.add(role_name)
                tags_resp = safe_call(lam.list_tags, Resource=fn.get("FunctionArn", "")) if fn.get("FunctionArn") else {}
                tags = tags_resp.get("Tags", {}) if "__error__" not in tags_resp else {}
                # LastModified is a plain string, so parse it explicitly.
                created_dt = parse_aws_ts_string(fn.get("LastModified"))
                evidence = cw_activity(
                    cw, "AWS/Lambda", "Invocations",
                    [{"Name": "FunctionName", "Value": name}], stat="Sum",
                    explanation="Invocations: the function actually ran")
                # LastModified is a configuration change: it fills "created", never last_used.
                last_used = evidence.last_activity
                inferred = not evidence.used
                ref_days = days_ago(last_used) if last_used else days_ago(created_dt)
                vpc_id = fn.get("VpcConfig", {}).get("VpcId", "") if fn.get("VpcConfig") else ""

                env_configs, alias_error = collect_lambda_function_env_configs(lam, name)
                latest_env = (fn.get("Environment", {}) or {}).get("Variables", {}) or {}
                all_env_configs = [("$LATEST", latest_env)] + env_configs

                connections = join_nonempty([
                    f"IAM role={role_name}" if role_name else "",
                    f"VPC={vpc_id}" if vpc_id else "",
                    f"aliases: {', '.join(label for label, _ in env_configs)}" if env_configs else "",
                ])
                notes = activity_note(
                    evidence,
                    "'created' is actually LastModified - Lambda API has no true creation "
                    "timestamp, and a recent value there means the function was EDITED, "
                    "not invoked.")
                if alias_error:
                    notes += (f" Could not list aliases ({alias_error}) - any resources referenced only by an "
                              "alias-pinned version's env vars are under-reported.")
                elif env_configs:
                    notes += (f" Env vars were read from $LATEST plus {len(env_configs)} alias-pinned "
                              "version(s), since published versions carry their own immutable env vars.")

                description = join_nonempty([fn.get("Description", ""), fn.get("Runtime", "")])
                row = new_row(
                    "LambdaFunction", region, name, name,
                    created_dt, last_used, ref_days, inferred,
                    flag_from_activity(evidence, days_ago(created_dt)),
                    notes,
                    tags=tags, description=description, connections=connections,
                    vpc_id=vpc_id, iam_role=role_name,
                    arn=fn.get("FunctionArn", ""), activity=evidence,
                )
                add_edge(row, role_name, "runs as", "function configuration Role",
                         conn_type="lambda.iamrole.execution-role",
                         target_service="IAMRole")
                for subnet_id in (fn.get("VpcConfig") or {}).get("SubnetIds") or []:
                    add_edge(row, subnet_id, "runs in subnet", "function VpcConfig.SubnetIds",
                             conn_type="lambda.subnet.placement", target_service="Subnet")
                add_edge(row, _image_repository(lam, fn), "runs image from", "GetFunction Code.ImageUri",
                         conn_type="lambda.ecrrepository.image", target_service="ECRRepository")
                raw_capture.record("LambdaFunction", region, name, fn)

                if console.VERBOSE:
                    say(f"    [explain] {name}: {len(all_env_configs)} config(s) "
                          f"({', '.join(q for q, _ in all_env_configs)})"
                          + (f" alias listing FAILED: {alias_error}" if alias_error else ""), flush=True)
                for qualifier, env in all_env_configs:
                    if console.VERBOSE and not env:
                        say(f"    [explain]   {qualifier}: (no environment variables)", flush=True)
                    for var_key, var_value in env.items():
                        candidates = env_value_resource_candidates(var_value)
                        if console.VERBOSE:
                            say(f"    [explain]   {qualifier}: {var_key}={var_value!r} -> "
                                  f"{[c for c, _, _, _ in candidates] or 'NO CANDIDATE EXTRACTED'}", flush=True)
                        for candidate, how, cand_service, cand_conn_type in candidates:
                            add_edge(row, candidate, "references (env var)",
                                     f"{qualifier} env var {var_key} - {how}",
                                     conn_type=cand_conn_type, assert_exists=False,
                                     target_service=cand_service)

                # A dead-letter target and an explicit log group are real,
                # AWS-configured links, unlike the env-var heuristic above.
                dlq_arn = (fn.get("DeadLetterConfig", {}) or {}).get("TargetArn")
                if dlq_arn:
                    dlq_id, dlq_service = probable_resource_id_from_arn(dlq_arn)
                    add_edge(row, dlq_id, "dead-letter queue/topic",
                             "Lambda DeadLetterConfig.TargetArn",
                             conn_type="lambda.any.dead-letter-config",
                             assert_exists=False, target_service=dlq_service)
                explicit_log_group = (fn.get("LoggingConfig", {}) or {}).get("LogGroup")
                if explicit_log_group:
                    add_edge(row, explicit_log_group, "logs to",
                             "Lambda LoggingConfig.LogGroup",
                             conn_type="lambda.loggroup.logging-config",
                             assert_exists=False, target_service="CloudWatchLogGroup")
                rows.append(row)
    except (ClientError, BotoCoreError) as e:
        rows.append(error_row("LambdaFunction", region, "ERROR", str(e)))

    _attach_event_source_mappings(lam, region, rows)
    return rows


def _attach_event_source_mappings(lam, region, rows):
    """What invokes each function, from event-source mappings (one call per region).
    Disabled mappings are still recorded: someone wired them together."""
    by_name = {row["resource_id"]: row for row in rows if row["flag"] != "ERROR"}
    if not by_name:
        return
    mappings, error = paged(lam, "list_event_source_mappings", "EventSourceMappings",
                            capability=coverage.DEPENDENCIES,
                            service="LambdaFunction")
    if error and not mappings:
        return
    for mapping in mappings:
        source_arn = mapping.get("EventSourceArn")
        function_arn = mapping.get("FunctionArn") or ""
        if not source_arn or not function_arn:
            continue
        # The row is keyed on the bare name; the alias/version qualifier is kept as evidence.
        function_name, _svc = probable_resource_id_from_arn(function_arn)
        qualifier = lambda_qualifier_from_arn(function_arn)
        row = by_name.get(function_name)
        if row is None:
            continue
        target_id, target_service = probable_resource_id_from_arn(source_arn)
        if not target_id:
            continue
        account, source_region = arn_scope(source_arn)
        state = mapping.get("State") or "unknown"
        add_edge(row, target_id, "consumes events from",
                 f"Lambda event source mapping {mapping.get('UUID', '')} "
                 f"(state: {state})",
                 conn_type="lambda.any.event-source-mapping",
                 target_service=target_service, target_arn=source_arn,
                 target_account=account, target_region=source_region,
                 target_qualifier=qualifier)
