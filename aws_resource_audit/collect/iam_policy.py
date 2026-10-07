"""IAM policy documents: parsing them, and reading the services a role trusts
and the resource ARNs its policies grant."""

import json
import logging
import urllib.parse

from .calls import safe_call

logger = logging.getLogger(__name__)


def role_name_from_arn(arn):
    if not arn:
        return None
    return arn.rsplit("/", 1)[-1]

def parse_policy_document(raw):
    """A policy document as a dict: botocore usually decodes it already, but a raw
    string is decoded too. None on anything malformed."""
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(urllib.parse.unquote(raw))
    except (ValueError, TypeError):
        # WARNING, unlike the two timestamp parsers above: a policy that does
        # not parse drops every resource grant in it, so links go missing.
        logger.warning("malformed IAM policy document, its grants will be "
                       "missing from connections: %.200r", raw)
        return None


def extract_trust_service_principals(trust_doc):
    """Service principals (e.g. lambda.amazonaws.com) a role's trust policy allows,
    which say what the role is for even for services this tool does not scan."""
    services = set()
    if not trust_doc:
        return services
    statements = trust_doc.get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    for stmt in statements:
        if stmt.get("Effect") != "Allow":
            continue
        principal = stmt.get("Principal")
        if not isinstance(principal, dict):
            continue
        svc = principal.get("Service")
        if svc:
            services.update([svc] if isinstance(svc, str) else svc)
    return services


def extract_resource_arns(policy_doc):
    """Resource ARNs an Allow statement grants. Partial wildcards are kept (they still
    name a resource); only a bare "*" is dropped."""
    arns = set()
    if not policy_doc:
        return arns
    statements = policy_doc.get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    for stmt in statements:
        if stmt.get("Effect") != "Allow":
            continue
        resource = stmt.get("Resource")
        if not resource:
            continue
        values = [resource] if isinstance(resource, str) else resource
        for v in values:
            if not isinstance(v, str) or not v.startswith("arn:"):
                continue
            parts = v.split(":", 5)
            if len(parts) == 6 and parts[5].strip("/") == "*":
                continue  # e.g. arn:aws:s3:::* - no resource-specific signal
            arns.add(v)
    return arns


def gather_role_policy_grants(iam, role_name):
    """Resource ARNs this role's own inline and customer-managed policies grant.
    AWS-managed policies are skipped: broad, and no grouping signal."""
    resource_arns = set()

    inline_resp = safe_call(iam.list_role_policies, RoleName=role_name)
    if "__error__" not in inline_resp:
        for pname in inline_resp.get("PolicyNames", []):
            detail = safe_call(iam.get_role_policy, RoleName=role_name, PolicyName=pname)
            if "__error__" not in detail:
                resource_arns.update(extract_resource_arns(parse_policy_document(detail.get("PolicyDocument"))))

    attached_resp = safe_call(iam.list_attached_role_policies, RoleName=role_name)
    if "__error__" not in attached_resp:
        for pol in attached_resp.get("AttachedPolicies", []):
            arn = pol["PolicyArn"]
            if ":aws:policy/" in arn:
                continue  # AWS-managed - skip, see docstring above
            pol_detail = safe_call(iam.get_policy, PolicyArn=arn)
            if "__error__" in pol_detail:
                continue
            version_id = pol_detail["Policy"]["DefaultVersionId"]
            version_detail = safe_call(iam.get_policy_version, PolicyArn=arn, VersionId=version_id)
            if "__error__" not in version_detail:
                resource_arns.update(extract_resource_arns(parse_policy_document(version_detail["PolicyVersion"]["Document"])))

    return resource_arns
