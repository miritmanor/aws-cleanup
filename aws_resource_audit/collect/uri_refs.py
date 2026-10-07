"""References hidden in strings: execute-api URLs, Lambda integration URIs,
stage variables, environment values and log group names."""

import re

from .arns import probable_resource_id_from_arn


EXECUTE_API_URL_RE = re.compile(r"https?://([a-z0-9]+)\.execute-api\.")


def apigateway_ids_from_execute_api_urls(value):
    """API Gateway ids in execute-api URLs within a string, e.g. "https://abc123defg.
    execute-api.us-east-1.amazonaws.com/prod/". Not type-namespaced."""
    if not value:
        return []
    return list(dict.fromkeys(EXECUTE_API_URL_RE.findall(value)))


def lambda_name_from_integration_uri(uri):
    """The Lambda function name in an API Gateway integration URI, or None."""
    if not uri:
        return None
    m = re.search(r"function:([^/]+)", uri)
    if not m:
        return None
    return m.group(1).split(":")[0]


def lambda_qualifier_from_arn(arn):
    """The alias or version a Lambda ARN pins ("...:function:name:prod" -> "prod"), or None."""
    if not arn or ":function:" not in arn:
        return None
    tail = arn.split(":function:", 1)[1]
    parts = tail.split(":")
    return parts[1] if len(parts) > 1 and parts[1] else None


def lambda_qualifier_from_integration_uri(uri):
    """The alias or version an integration URI pins, or None; kept as evidence."""
    if not uri:
        return None
    m = re.search(r"function:([^/]+)", uri)
    if not m:
        return None
    parts = m.group(1).split(":")
    return parts[1] if len(parts) > 1 and parts[1] else None


STAGE_VARIABLE_RE = re.compile(r"\$\{stageVariables\.([A-Za-z0-9_]+)\}")


def resolve_stage_variables(uri, stage_variable_maps):
    """Substitute ${stageVariables.X} per deployed stage; returns every distinct result.
    Undefined placeholders are left alone so the URI still fails to resolve."""
    if not uri or "${stageVariables." not in uri:
        return []
    resolved = []
    for variables in stage_variable_maps or []:
        out = STAGE_VARIABLE_RE.sub(
            lambda m: variables.get(m.group(1), m.group(0)), uri)
        if "${stageVariables." not in out and out not in resolved:
            resolved.append(out)
    return resolved


def env_value_resource_candidates(value):
    """Possible resource ids in an env var value: [(id, how, service, conn_type)]. The value is
    an ARN, contains one, is an execute-api URL, or is a bare name (weakest, report-only)."""
    if not isinstance(value, str) or not value:
        return []
    out = []
    if value.startswith("arn:"):
        rid, target_service = probable_resource_id_from_arn(value)
        if rid:
            out.append((rid, "env var value is an ARN", target_service,
                        "lambda.any.env-var-arn-value"))
    else:
        for m in re.finditer(r"arn:aws[a-z-]*:[^\s\"',}\]]+", value):
            rid, target_service = probable_resource_id_from_arn(m.group(0))
            if rid:
                out.append((rid, "ARN embedded in env var value", target_service,
                            "lambda.any.env-var-arn-embedded"))
        for api_id in apigateway_ids_from_execute_api_urls(value)[:1]:
            out.append((api_id, "API Gateway id from execute-api URL in env var", None,
                        "lambda.apigateway.env-var-execute-api-url"))
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,254}", value):
            out.append((value, "env var value matches a scanned resource name exactly", None,
                        "lambda.any.env-var-bare-name"))
    return out


def infer_log_group_source(log_group_name):
    """Infer a log group's source from AWS naming: (label, embedded_id, group_match_id,
    group_match_service). Match ids only for names AWS itself enforces."""
    name = log_group_name or ""

    m = re.match(r"^/aws/lambda/(.+)$", name)
    if m:
        return "Lambda function (AWS-enforced naming)", m.group(1), m.group(1), "LambdaFunction"

    m = re.match(r"^API-Gateway-Execution-Logs_([A-Za-z0-9]+)/(.+)$", name)
    if m:
        return (f"API Gateway REST API execution logs, stage '{m.group(2)}' (AWS-enforced naming)",
                m.group(1), m.group(1), "APIGatewayRestApi")

    m = re.match(r"^/aws/rds/instance/([^/]+)/(.+)$", name)
    if m:
        return f"RDS instance log export: {m.group(2)} (AWS-enforced naming)", m.group(1), m.group(1), "RDSInstance"

    m = re.match(r"^/aws/rds/cluster/([^/]+)/(.+)$", name)
    if m:
        return f"RDS (Aurora) cluster log export: {m.group(2)} (AWS-enforced naming)", m.group(1), m.group(1), "RDSCluster"

    m = re.match(r"^/aws/amplify/([^/]+)/(.+)$", name)
    if m:
        return f"Amplify app, branch '{m.group(2)}' (AWS-enforced naming)", m.group(1), m.group(1), "AmplifyApp"

    m = re.match(r"^/aws/codebuild/(.+)$", name)
    if m:
        return "CodeBuild project (default naming, can be overridden - not linked)", m.group(1), None, None

    m = re.match(r"^/ecs/(.+)$", name)
    if m:
        return "Possibly an ECS task/service (naming is a common convention, not enforced - not linked)", m.group(1), None, None

    m = re.match(r"^/aws/eks/([^/]+)/cluster$", name)
    if m:
        return "EKS cluster control plane logs (AWS-enforced naming - not scanned by this script)", m.group(1), None, None

    if re.match(r"^/aws/vendedlogs/states/", name):
        return "Likely a Step Functions state machine (common but user-chosen naming - not linked)", None, None, None

    if re.search(r"flow-?logs?", name, re.IGNORECASE):
        return "Likely VPC Flow Logs (name suggests it, not enforced)", None, None, None

    if "cloudtrail" in name.lower():
        return "Possibly a CloudTrail delivery target - cross-check CloudTrailTrail rows' connections for an exact ARN match", None, None, None

    return None, None, None, None


ECR_IMAGE_RE = re.compile(r"^\d+\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/([^:@]+)")


def ecr_repository_from_image(uri):
    """The ECR repository an image URI pulls from, or None for any other registry."""
    match = ECR_IMAGE_RE.match(uri or "")
    return match.group(1) if match else None
