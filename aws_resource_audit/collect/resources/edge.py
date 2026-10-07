"""Front doors: API Gateway (REST and HTTP/WebSocket) and load balancers; integrations
are read off each method, so the links are AWS's own routing config."""

import logging

from botocore.exceptions import BotoCoreError, ClientError

from ..arns import probable_resource_id_from_arn
from ..calls import paged
from ..cloudwatch import activity_note, cw_activity
from ..uri_refs import (
    lambda_qualifier_from_integration_uri,
    lambda_name_from_integration_uri,
    resolve_stage_variables,
)
from ...config import RETRY_CONFIG
from ... import activity, coverage
from ...staleness import days_ago, flag_from_activity
from ...collect.calls import safe_call, tags_to_dict
from ...text import join_nonempty
from ...rows import add_edge, error_row, new_row
from ...collect import raw_capture

logger = logging.getLogger(__name__)


def collect_load_balancers(session, region):
    rows = []
    try:
        elbv2 = session.client("elbv2", region_name=region, config=RETRY_CONFIG)
        cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
        paginator = elbv2.get_paginator("describe_load_balancers")
        for page in paginator.paginate():
            for lb in page["LoadBalancers"]:
                arn = lb["LoadBalancerArn"]
                name = lb["LoadBalancerName"]
                created = lb.get("CreatedTime")
                tags_resp = safe_call(elbv2.describe_tags, ResourceArns=[arn])
                tags = {}
                if "__error__" not in tags_resp and tags_resp.get("TagDescriptions"):
                    tags = tags_to_dict(tags_resp["TagDescriptions"][0].get("Tags", []))
                metric_name = "RequestCount" if lb["Type"] == "application" else "ActiveFlowCount"
                dim_suffix = "/".join(arn.split("/")[1:])  # LB dimension format
                evidence = cw_activity(
                    cw, "AWS/ApplicationELB" if lb["Type"] == "application" else "AWS/NetworkELB",
                    metric_name, [{"Name": "LoadBalancerName", "Value": dim_suffix}], stat="Sum"
                )
                last_used = evidence.last_activity
                inferred = not evidence.used
                ref_days = days_ago(last_used) if last_used else days_ago(created)
                vpc_id = lb.get("VpcId", "")
                sg_ids = lb.get("SecurityGroups", [])
                connections = join_nonempty([
                    f"VPC={vpc_id}" if vpc_id else "",
                    f"SGs=[{','.join(sg_ids)}]" if sg_ids else "",
                ])
                rows.append(new_row(
                    "LoadBalancer", region, name, lb["Type"],
                    created, last_used, ref_days, inferred,
                    flag_from_activity(evidence, days_ago(created)),
                    activity_note(evidence, f"{metric_name} is the activity proxy for this load balancer."),
                    tags=tags, description=lb.get("DNSName", ""), connections=connections, vpc_id=vpc_id,
                    arn=arn,
                ))
                raw_capture.record("LoadBalancer", region, name, lb)
                for zone in lb.get("AvailabilityZones") or []:
                    add_edge(rows[-1], zone.get("SubnetId"), "listens in subnet",
                             "load balancer AvailabilityZones.SubnetId",
                             conn_type="loadbalancer.subnet.placement", target_service="Subnet")
                _attach_lb_targets(elbv2, region, rows[-1], arn)
    except (ClientError, BotoCoreError) as e:
        rows.append(error_row("LoadBalancer", region, "ERROR", str(e)))
    return rows


def collect_classic_load_balancers(session, region):
    """Classic (v1) load balancers, invisible to the elbv2 API but billed hourly
    all the same. Emitted as LoadBalancer rows; activity is AWS/ELB RequestCount."""
    elb = session.client("elb", region_name=region, config=RETRY_CONFIG)
    lbs, page_error = paged(elb, "describe_load_balancers", "LoadBalancerDescriptions",
                            service="LoadBalancer")
    if page_error and not lbs:
        return [error_row("LoadBalancer", region, "ERROR", page_error)]
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    for lb in lbs:
        name = lb["LoadBalancerName"]
        created = lb.get("CreatedTime")
        tags_resp = safe_call(elb.describe_tags, LoadBalancerNames=[name])
        tags = {}
        if "__error__" not in tags_resp and tags_resp.get("TagDescriptions"):
            tags = tags_to_dict(tags_resp["TagDescriptions"][0].get("Tags", []))
        evidence = cw_activity(cw, "AWS/ELB", "RequestCount",
                               [{"Name": "LoadBalancerName", "Value": name}], stat="Sum")
        last_used = evidence.last_activity
        vpc_id = lb.get("VPCId", "")
        row = new_row(
            "LoadBalancer", region, name, "classic", created, last_used,
            days_ago(last_used) if last_used else days_ago(created), not evidence.used,
            flag_from_activity(evidence, days_ago(created)),
            activity_note(evidence, "RequestCount is the activity proxy for this load balancer.")
            + " Classic Load Balancer: AWS recommends migrating to an Application or Network Load Balancer. ",
            tags=tags, description=lb.get("DNSName", ""),
            connections=f"VPC={vpc_id}" if vpc_id else "", vpc_id=vpc_id)
        raw_capture.record("LoadBalancer", region, name, lb)
        for instance in lb.get("Instances", []):
            add_edge(row, instance.get("InstanceId"), "routes to", "classic ELB Instances",
                     conn_type="loadbalancer.any.target", target_service="EC2Instance")
        for subnet_id in lb.get("Subnets", []):
            add_edge(row, subnet_id, "listens in subnet", "classic ELB Subnets",
                     conn_type="loadbalancer.subnet.placement", target_service="Subnet")
        rows.append(row)
    return rows


def _attach_lb_targets(elbv2, region, row, lb_arn):
    """What the load balancer routes to: target groups and their registered targets.
    Target health is evidence only - unhealthy is not abandoned."""
    groups, error = paged(elbv2, "describe_target_groups", "TargetGroups",
                          capability=coverage.DEPENDENCIES,
                          service="LoadBalancer", LoadBalancerArn=lb_arn)
    if error and not groups:
        return
    for group in groups:
        group_arn = group.get("TargetGroupArn")
        group_name = group.get("TargetGroupName") or ""
        target_type = group.get("TargetType") or "instance"
        if not group_arn:
            continue
        health = safe_call(elbv2.describe_target_health,
                           capability=coverage.DEPENDENCIES,
                           TargetGroupArn=group_arn)
        if "__error__" in health:
            continue
        for entry in health.get("TargetHealthDescriptions", []):
            target_id = (entry.get("Target") or {}).get("Id")
            if not target_id:
                continue
            state = (entry.get("TargetHealth") or {}).get("State") or "unknown"
            # An IP target names no resource this script inventories, and a
            # raw address must not be matched against anything by accident.
            if target_type == "ip":
                continue
            service = {"instance": "EC2Instance",
                       "lambda": "LambdaFunction",
                       "alb": "LoadBalancer"}.get(target_type)
            if target_type == "lambda":
                target_id, _svc = probable_resource_id_from_arn(target_id)
                if not target_id:
                    continue
            add_edge(row, target_id, "routes to",
                     f"target group {group_name} ({target_type}, health: {state})",
                     conn_type="loadbalancer.any.target",
                     target_service=service, target_region=region)


def find_lambda_integrations_rest_api(apigw, api_id, stage_variable_maps=None):
    """Target Lambdas of a REST API's integrations, as (direct_names, stage_variable_names,
    error). An error means "integrations unknown", not "none"."""
    direct, via_stage = set(), set()
    try:
        for page in apigw.get_paginator("get_resources").paginate(restApiId=api_id):
            for res in page.get("items", []):
                for method in (res.get("resourceMethods") or {}).keys():
                    integ = safe_call(
                        apigw.get_integration, restApiId=api_id,
                        resourceId=res["id"], httpMethod=method,
                    )
                    if "__error__" in integ:
                        continue
                    if integ.get("type") not in ("AWS_PROXY", "AWS"):
                        continue
                    uri = integ.get("uri")
                    lname = lambda_name_from_integration_uri(uri)
                    if lname and "${" not in lname:
                        direct.add((lname, lambda_qualifier_from_integration_uri(uri)))
                        continue
                    for resolved_uri in resolve_stage_variables(uri, stage_variable_maps):
                        rname = lambda_name_from_integration_uri(resolved_uri)
                        if rname and "${" not in rname:
                            via_stage.add((
                                rname,
                                lambda_qualifier_from_integration_uri(resolved_uri)))
    except (ClientError, BotoCoreError) as e:
        logger.warning("reading API Gateway integrations failed, lambda links "
                       "under-reported: %s", e)
        return direct, via_stage, str(e)
    return direct, via_stage, None


def _link_rest_authorizers(apigw, row, api_id, region):
    """The Cognito user pools and Lambda functions a REST API asks before it answers."""
    resp = safe_call(apigw.get_authorizers, restApiId=api_id)
    for auth in [] if "__error__" in resp else resp.get("items", []):
        evidence = f"authorizer {auth.get('name', '')}"
        for arn in auth.get("providerARNs") or []:
            pool, _service = probable_resource_id_from_arn(arn)
            add_edge(row, pool, "authorizes with", evidence,
                     conn_type="apigateway.cognito.authorizer", target_service="CognitoUserPool")
        add_edge(row, lambda_name_from_integration_uri(auth.get("authorizerUri")), "authorizes with",
                 evidence, conn_type="apigateway.lambda.authorizer",
                 target_service="LambdaFunction", target_region=region)


def _link_v2_authorizers(apigwv2, row, api_id, region):
    """An HTTP API's JWT authorizer names a Cognito pool by its issuer URL."""
    resp = safe_call(apigwv2.get_authorizers, ApiId=api_id)
    for auth in [] if "__error__" in resp else resp.get("Items", []):
        evidence = f"authorizer {auth.get('Name', '')}"
        issuer = ((auth.get("JwtConfiguration") or {}).get("Issuer") or "").rstrip("/")
        if "://cognito-idp." in issuer:
            add_edge(row, issuer.rsplit("/", 1)[-1], "authorizes with", evidence,
                     conn_type="apigatewayv2.cognito.authorizer", target_service="CognitoUserPool")
        add_edge(row, lambda_name_from_integration_uri(auth.get("AuthorizerUri")), "authorizes with",
                 evidence, conn_type="apigatewayv2.lambda.authorizer",
                 target_service="LambdaFunction", target_region=region)


def collect_api_gateway_rest_apis(session, region):
    apigw = session.client("apigateway", region_name=region, config=RETRY_CONFIG)
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    try:
        paginator = apigw.get_paginator("get_rest_apis")
        for page in paginator.paginate():
            for api in page["items"]:
                api_id = api["id"]
                name = api.get("name", "")
                created = api.get("createdDate")
                tags = api.get("tags", {}) or {}
                stages_resp = safe_call(apigw.get_stages, restApiId=api_id)
                stages = stages_resp.get("item", []) if "__error__" not in stages_resp else []
                stage_names = [s["stageName"] for s in stages]
                # Each stage's variables are what a ${stageVariables.X} in an
                # integration URI actually resolves to - see resolve_stage_variables.
                stage_variable_maps = [s.get("variables") or {} for s in stages]
                evidence = activity.best_of(*[
                    cw_activity(
                        cw, "AWS/ApiGateway", "Count",
                        [{"Name": "ApiName", "Value": name},
                         {"Name": "Stage", "Value": stage}], stat="Sum",
                        explanation=f"stage {stage}")
                    for stage in stage_names])
                last_used = evidence.last_activity
                inferred = not evidence.used
                ref_days = days_ago(last_used) if last_used else days_ago(created)

                lambda_names, stage_var_lambdas, integ_error = find_lambda_integrations_rest_api(
                    apigw, api_id, stage_variable_maps)
                notes = activity_note(evidence, "Count CloudWatch metric summed across all stages used as activity proxy")
                if integ_error:
                    notes += f" Could not fully scan integrations ({integ_error}) - Lambda backends may be under-reported."

                row = new_row(
                    "APIGatewayRestApi", region, api_id, name,
                    created, last_used, ref_days, inferred,
                    flag_from_activity(evidence, days_ago(created)),
                    notes, tags=tags, description=api.get("description", ""),
                )
                raw_capture.record("APIGatewayRestApi", region, api_id, api)
                for lname, qualifier in sorted(lambda_names):
                    add_edge(row, lname, "invokes",
                             "API Gateway method integration URI (get_integration)"
                             + (f" pinned to '{qualifier}'" if qualifier else ""),
                             conn_type="apigateway.lambda.integration",
                             target_service="LambdaFunction", target_region=region,
                             target_qualifier=qualifier)
                for lname, qualifier in sorted(stage_var_lambdas - lambda_names):
                    add_edge(row, lname, "invokes",
                             "API Gateway integration URI ${stageVariables.*} resolved from stage config"
                             + (f" pinned to '{qualifier}'" if qualifier else ""),
                             conn_type="apigateway.lambda.stage-variable",
                             target_service="LambdaFunction", target_region=region,
                             target_qualifier=qualifier)
                _link_rest_authorizers(apigw, row, api_id, region)
                rows.append(row)
    except (ClientError, BotoCoreError) as e:
        rows.append(error_row("APIGatewayRestApi", region, "ERROR", str(e)))
    return rows


def collect_api_gateway_v2_apis(session, region):
    apigwv2 = session.client("apigatewayv2", region_name=region, config=RETRY_CONFIG)
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    try:
        paginator = apigwv2.get_paginator("get_apis")
        for page in paginator.paginate():
            for api in page["Items"]:
                api_id = api["ApiId"]
                name = api.get("Name", "")
                created = api.get("CreatedDate")
                tags = api.get("Tags", {}) or {}
                evidence = cw_activity(
                    cw, "AWS/ApiGateway", "Count",
                    [{"Name": "ApiId", "Value": api_id}], stat="Sum"
                )
                last_used = evidence.last_activity
                inferred = not evidence.used
                ref_days = days_ago(last_used) if last_used else days_ago(created)

                # v2 lists every integration in one call, with the Lambda in IntegrationUri.
                lambda_names = set()
                integ_resp = safe_call(apigwv2.get_integrations, ApiId=api_id)
                notes = activity_note(evidence, "Count CloudWatch metric used as activity proxy (HTTP/WebSocket API)")
                if "__error__" in integ_resp:
                    notes += f" Could not read integrations ({integ_resp['__error__']}) - Lambda backends may be under-reported."
                else:
                    for integ in integ_resp.get("Items", []):
                        if integ.get("IntegrationType") == "AWS_PROXY":
                            uri = integ.get("IntegrationUri")
                            lname = lambda_name_from_integration_uri(uri)
                            if lname:
                                lambda_names.add(
                                    (lname, lambda_qualifier_from_integration_uri(uri)))

                row = new_row(
                    "APIGatewayV2Api", region, api_id, name,
                    created, last_used, ref_days, inferred,
                    flag_from_activity(evidence, days_ago(created)),
                    notes, tags=tags,
                )
                raw_capture.record("APIGatewayV2Api", region, api_id, api)
                for lname, qualifier in sorted(lambda_names):
                    add_edge(row, lname, "invokes",
                             "HTTP/WebSocket API integration IntegrationUri (get_integrations)"
                             + (f" pinned to '{qualifier}'" if qualifier else ""),
                             conn_type="apigatewayv2.lambda.integration",
                             target_service="LambdaFunction", target_region=region,
                             target_qualifier=qualifier)
                _link_v2_authorizers(apigwv2, row, api_id, region)
                rows.append(row)
    except (ClientError, BotoCoreError) as e:
        rows.append(error_row("APIGatewayV2Api", region, "ERROR", str(e)))
    return rows


def _rest_mappings(apigw, domain):
    resp = safe_call(apigw.get_base_path_mappings, domainName=domain)
    return [] if "__error__" in resp else [m for m in resp.get("items", []) if m.get("restApiId")]


def _v2_mappings(apigwv2, domain, rest_ids):
    """HTTP/WebSocket APIs mapped to a regional domain. The v2 call lists REST
    mappings too, so ids already found as REST APIs are left out."""
    items, _error = paged(apigwv2, "get_api_mappings", "Items", service="APIGatewayDomainName",
                          DomainName=domain)
    return [m for m in items if m.get("ApiId") and m["ApiId"] not in rest_ids]


def collect_api_gateway_domain_names(session, region):
    """Custom domain names: free, and what a Route 53 record for api.example.com
    actually points at. Each maps base paths to REST, HTTP or WebSocket APIs."""
    apigw = session.client("apigateway", region_name=region, config=RETRY_CONFIG)
    apigwv2 = session.client("apigatewayv2", region_name=region, config=RETRY_CONFIG)
    domains, page_error = paged(apigw, "get_domain_names", "items", service="APIGatewayDomainName")
    if page_error and not domains:
        return [error_row("APIGatewayDomainName", region, "ERROR", page_error)]
    rows = []
    for domain in domains:
        name = domain["domainName"]
        types = (domain.get("endpointConfiguration") or {}).get("types") or []
        rest = _rest_mappings(apigw, name)
        rest_ids = {m["restApiId"] for m in rest}
        v2 = _v2_mappings(apigwv2, name, rest_ids) if "REGIONAL" in types else []
        count = len(rest_ids) + len({m["ApiId"] for m in v2})
        flag = (f"ACTIVE (maps to {count} API(s))" if count
                else "STALE (maps to no API - requests to it fail)")
        row = new_row(
            "APIGatewayDomainName", region, name, name, None, None, None, True, flag,
            "A custom domain name is free; its certificate lives in ACM. ",
            tags=dict(domain.get("tags") or {}),
            description=join_nonempty(["/".join(types),
                                       domain.get("regionalDomainName") or domain.get("distributionDomainName")], " "),
            arn=f"arn:aws:apigateway:{region}::/domainnames/{name}")
        raw_capture.record("APIGatewayDomainName", region, name, domain)
        for mapping in rest:
            add_edge(row, mapping["restApiId"], "routes to API", f"base path {mapping.get('basePath', '(none)')}",
                     conn_type="apidomain.apigateway.mapping", target_service="APIGatewayRestApi")
        for mapping in v2:
            add_edge(row, mapping["ApiId"], "routes to API", f"API mapping {mapping.get('ApiMappingKey') or '(none)'}",
                     conn_type="apidomain.apigatewayv2.mapping", target_service="APIGatewayV2Api")
        rows.append(row)
    return rows
