"""Following AWS's own record of what an Amplify deployment created: the backend's
CloudFormation stack tree, plus API ids named in app environment variables."""

import logging
import re
from dataclasses import dataclass, field

from botocore.exceptions import BotoCoreError, ClientError

from ..config import RETRY_CONFIG
from ..rows import add_edge
from .calls import safe_call
from .uri_refs import apigateway_ids_from_execute_api_urls

logger = logging.getLogger(__name__)


def normalize_for_name_match(s):
    """Lowercase, alnum-only - used only for the Amplify<->API Gateway name
    heuristic below, never for anything treated as authoritative."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def list_stack_physical_resources(cfn, stack_name, seen=None, depth=0, max_depth=6,
                                  provenance=None, category=""):
    """Walk a stack and its nested stacks: {ResourceType: set(PhysicalResourceId)}, nested
    stacks included. `provenance` gets logical_id and outermost category per resource."""
    if seen is None:
        seen = set()
    result = {}
    if not stack_name or stack_name in seen or depth > max_depth:
        return result
    seen.add(stack_name)
    try:
        for page in cfn.get_paginator("list_stack_resources").paginate(StackName=stack_name):
            for res in page.get("StackResourceSummaries", []):
                rtype = res.get("ResourceType")
                pid = res.get("PhysicalResourceId")
                if not pid:
                    continue
                logical = res.get("LogicalResourceId") or ""
                if provenance is not None:
                    provenance.setdefault(pid, {
                        "logical_id": logical, "category": category, "type": rtype,
                    })
                if rtype == "AWS::CloudFormation::Stack":
                    result.setdefault(rtype, set()).add(pid)
                    nested = list_stack_physical_resources(
                        cfn, pid, seen, depth + 1, max_depth,
                        provenance=provenance, category=category or logical.lower())
                    for k, v in nested.items():
                        result.setdefault(k, set()).update(v)
                else:
                    result.setdefault(rtype, set()).add(pid)
    except (ClientError, BotoCoreError) as e:
        # Non-fatal by design, but never silent: this walk is the authoritative
        # evidence behind Amplify grouping, so losing it changes the output.
        logger.warning("Amplify stack walk stopped at %s (%s); backend "
                       "resources found so far are all that will be linked",
                       stack_name, e, exc_info=True)
    return result


# CloudFormation types that map onto a service this tool scans directly; includes
# nested stacks so they join the app's project group.
AMPLIFY_OWNED_RESOURCE_TYPES = (
    "AWS::ApiGateway::RestApi", "AWS::ApiGatewayV2::Api", "AWS::Lambda::Function",
    "AWS::DynamoDB::Table", "AWS::S3::Bucket", "AWS::Cognito::UserPool",
    "AWS::CloudFormation::Stack",
)


@dataclass
class AmplifyBackend:
    """What one app's backend environments actually provisioned."""

    by_type: dict = field(default_factory=dict)
    root_stack_names: set = field(default_factory=set)
    # {PhysicalResourceId: {"logical_id", "category", "type"}} - see
    # list_stack_physical_resources.
    provenance: dict = field(default_factory=dict)
    # Buckets Amplify itself names as its deployment artifact store.
    deployment_artifacts: set = field(default_factory=set)


def find_amplify_backend_resources(session, region, app_id):
    """Gen 1 backends: app -> backend environments -> root stack -> everything provisioned.
    Root stacks are matched by name (the API returns no ARN); Gen 2 apps are not walked."""
    amp = session.client("amplify", region_name=region, config=RETRY_CONFIG)
    cfn = session.client("cloudformation", region_name=region, config=RETRY_CONFIG)
    envs_resp = safe_call(amp.list_backend_environments, appId=app_id)
    if "__error__" in envs_resp:
        return AmplifyBackend()
    backend = AmplifyBackend()
    for env in envs_resp.get("backendEnvironments", []):
        stack_name = env.get("stackName")
        if stack_name:
            backend.root_stack_names.add(stack_name)
    # From the same response: an S3 bucket name, matchable with no parsing.
        artifacts = env.get("deploymentArtifacts")
        if artifacts:
            backend.deployment_artifacts.add(artifacts)
        nested = list_stack_physical_resources(cfn, stack_name,
                                               provenance=backend.provenance)
        for k, v in nested.items():
            backend.by_type.setdefault(k, set()).update(v)
    return backend


def find_amplify_env_var_api_ids(session, region, app_id):
    """API Gateway ids named by execute-api URLs in the app's and each branch's
    environment: {api_id: ["app" | "branch <name>", ...]}. Non-fatal throughout."""
    amp = session.client("amplify", region_name=region, config=RETRY_CONFIG)
    found = {}

    def scan(variables, where):
        for value in (variables or {}).values():
            for api_id in apigateway_ids_from_execute_api_urls(str(value)):
                if where not in found.setdefault(api_id, []):
                    found[api_id].append(where)

    app_resp = safe_call(amp.get_app, appId=app_id)
    if "__error__" not in app_resp:
        scan(app_resp.get("app", {}).get("environmentVariables"), "app")
    branches_resp = safe_call(amp.list_branches, appId=app_id)
    if "__error__" not in branches_resp:
        for branch in branches_resp.get("branches", []):
            scan(branch.get("environmentVariables"),
                 f"branch {branch.get('branchName', '?')}")
    return found


def apply_amplify_api_links(all_rows, session):
    """Link each Amplify app to what its backend provisioned (authoritative stack walk),
    plus a report-only name match for APIs the walk did not claim."""
    api_rows = [r for r in all_rows if r["service"] in ("APIGatewayRestApi", "APIGatewayV2Api")]
    for r in all_rows:
        if r["service"] != "AmplifyApp":
            continue

        backend = find_amplify_backend_resources(session, r["region"], r["resource_id"])
        root_stack_names = backend.root_stack_names
        owned_ids = set()
        for rtype in AMPLIFY_OWNED_RESOURCE_TYPES:
            owned_ids.update(backend.by_type.get(rtype, ()))

        # Facts only; analyze/tiers.py turns them into a runtime/deployment verdict.
        if backend.provenance:
            r["_amplify_provenance"] = backend.provenance
        if backend.deployment_artifacts:
            r["_amplify_deployment_artifacts"] = sorted(backend.deployment_artifacts)

        if owned_ids or root_stack_names:
            counts = ", ".join(f"{rtype.rsplit('::', 1)[-1]}={len(ids)}"
                               for rtype, ids in backend.by_type.items() if ids)
            stack_part = f"root stack(s)={len(root_stack_names)}" + (f", {counts}" if counts else "")
            extra = f"backend resources from this app's CloudFormation stack (authoritative, not name-matched): {stack_part}"
            r["connections"] = " | ".join(p for p in [r["connections"], extra] if p)
            for pid in owned_ids:
                add_edge(r, pid, "provisions (Amplify backend)",
                         "CloudFormation stack resources of this app's backend environment",
                         conn_type="amplify.any.cfn-stack-walk", assert_exists=False,
                         target_region=r.get("region"))
            for stack_name in root_stack_names:
                add_edge(r, stack_name, "backend root stack",
                         "Amplify list_backend_environments stackName",
                         conn_type="amplify.cfnstack.root-stack",
                         assert_exists=False, target_match="name",
                         target_region=r.get("region"))

        # What the app's own configuration says it calls. Read before the name
        # match so an API named outright is never also guessed at by name.
        env_api_ids = find_amplify_env_var_api_ids(session, r["region"], r["resource_id"])
        if env_api_ids:
            described = ", ".join(f"{api_id} (from {', '.join(where)})"
                                  for api_id, where in sorted(env_api_ids.items()))
            extra = f"API Gateway endpoint(s) configured in this app's environment: {described}"
            r["connections"] = " | ".join(p for p in [r["connections"], extra] if p)
            for api_id, where in sorted(env_api_ids.items()):
                # No target_service: an execute-api URL cannot say whether the
                # id belongs to a REST or an HTTP API.
                add_edge(r, api_id, "calls",
                         f"execute-api URL in {', '.join(where)} environment variable",
                         conn_type="amplify.apigateway.env-var-execute-api-url",
                         assert_exists=False)

        # Runs even when the stack walk worked: the walk sees what Amplify provisioned,
        # not pre-existing APIs the front end calls. Still a report-only guess.
        app_norm = normalize_for_name_match(r["name"])
        if len(app_norm) < 4:
            continue  # too short to match on safely
        matches = [a for a in api_rows
                   if a["resource_id"] not in owned_ids
                   and a["resource_id"] not in env_api_ids
                   and a["name"] and len(normalize_for_name_match(a["name"])) >= 4 and (
                       app_norm in normalize_for_name_match(a["name"])
                       or normalize_for_name_match(a["name"]) in app_norm
                   )]
        if matches:
            match_desc = ", ".join(f"{a['service']} {a['name'] or a['resource_id']}" for a in matches)
            provisioned = " this app's backend did not provision" if (owned_ids or root_stack_names) else ""
            extra = (f"possible API Gateway backend{provisioned} "
                     f"(NAME-MATCH HEURISTIC, LOW CONFIDENCE - verify manually): {match_desc}")
            r["connections"] = " | ".join(p for p in [r["connections"], extra] if p)
            for a in matches:
                add_edge(r, a["resource_id"], "possible backend",
                         "Amplify app name resembles this API's name",
                         conn_type="amplify.apigateway.name-match", assert_exists=False)
