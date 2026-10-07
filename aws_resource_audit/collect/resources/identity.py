"""IAM, Cognito, and IAM Access Analyzer's unused-role findings."""

import logging

from botocore.exceptions import BotoCoreError, ClientError

from ..arns import (
    apigateway_id_from_execute_api_arn,
    arn_scope,
    probable_resource_id_from_arn,
    resource_id_prefix_from_arn,
)
from ..iam_policy import (
    extract_resource_arns,
    extract_trust_service_principals,
    gather_role_policy_grants,
    parse_policy_document,
    role_name_from_arn,
)
from ...config import RETRY_CONFIG
from ...staleness import days_ago, flag_stale
from ...collect.calls import safe_call
from ...text import join_nonempty
from ...registry import detection_enabled
from ...rows import add_edge, error_row, new_row
from ...collect import raw_capture

logger = logging.getLogger(__name__)


def collect_cognito_user_pools(session, region):
    cip = session.client("cognito-idp", region_name=region, config=RETRY_CONFIG)
    rows = []
    try:
        paginator = cip.get_paginator("list_user_pools")
        for page in paginator.paginate(MaxResults=60):
            for pool in page["UserPools"]:
                pool_id = pool["Id"]
                name = pool.get("Name", "")
                created = pool.get("CreationDate")
                updated = pool.get("LastModifiedDate")
                ref_days = days_ago(updated) if updated else days_ago(created)
                notes = ("LastModifiedDate used as activity proxy (pool config changes, not actual sign-ins); "
                         "enable Cognito Advanced Security or check application logs for real sign-in activity")
                # Lambda triggers and the SMS role are only on describe_user_pool.
                detail = safe_call(cip.describe_user_pool, UserPoolId=pool_id)
                pool_detail = {} if "__error__" in detail else detail.get("UserPool", {})
                if "__error__" in detail:
                    notes += (f" Could not read pool detail ({detail['__error__']}) - "
                              "Lambda triggers and SMS role are not reported.")
                row = new_row(
                    "CognitoUserPool", region, pool_id, name,
                    created, updated, ref_days, True,
                    flag_stale(ref_days, None, True), notes,
                )
                # The describe payload when it was readable, else the listing
                # entry - pool_detail is what the triggers below are read from.
                raw_capture.record("CognitoUserPool", region, pool_id,
                                   pool_detail or pool)
                for trigger, value in sorted((pool_detail.get("LambdaConfig") or {}).items()):
                    # Most triggers map to a bare ARN string; the custom
                    # sender ones nest it under a dict with a key version.
                    arn = value.get("LambdaArn") if isinstance(value, dict) else value
                    fname, _svc = probable_resource_id_from_arn(arn)
                    if fname:
                        add_edge(row, fname, "invokes",
                                 f"Cognito {trigger} Lambda trigger (describe_user_pool LambdaConfig)",
                                 conn_type="cognito.lambda.trigger", target_service="LambdaFunction")
                sms_arn = (pool_detail.get("SmsConfiguration") or {}).get("SnsCallerArn")
                role_name, _svc = probable_resource_id_from_arn(sms_arn)
                if role_name:
                    add_edge(row, role_name, "sends SMS as",
                             "Cognito SmsConfiguration.SnsCallerArn (describe_user_pool)",
                             conn_type="cognito.iamrole.sms-caller", target_service="IAMRole")
                rows.append(row)
    except (ClientError, BotoCoreError) as e:
        rows.append(error_row("CognitoUserPool", region, "ERROR", str(e)))
    return rows


def collect_access_analyzer_unused_roles(session, region, unused_access_role_names=None):
    """Roles IAM Access Analyzer's unused-access analyzer flagged. Read-only: a no-op
    unless such an analyzer already exists."""
    aa = session.client("accessanalyzer", region_name=region, config=RETRY_CONFIG)
    analyzers_resp = safe_call(aa.list_analyzers)
    if "__error__" in analyzers_resp:
        return [error_row("IAMRoleUnusedAccessFinding", region, "ERROR", analyzers_resp["__error__"])]
    unused_analyzers = [
        a for a in analyzers_resp.get("analyzers", [])
        if a.get("type") in ("ACCOUNT_UNUSED_ACCESS", "ORGANIZATION_UNUSED_ACCESS") and a.get("status") == "ACTIVE"
    ]
    if not unused_analyzers:
        return []
    rows = []
    try:
        for analyzer in unused_analyzers:
            paginator = aa.get_paginator("list_findings_v2")
            for page in paginator.paginate(
                analyzerArn=analyzer["arn"],
                filter={
                    "resourceType": {"eq": ["AWS::IAM::Role"]},
                    "status": {"eq": ["ACTIVE"]},
                },
            ):
                for f in page.get("findings", []):
                    if f.get("findingType") != "UnusedIAMRole":
                        continue
                    role_arn = f.get("resource", "")
                    role_name = role_name_from_arn(role_arn)
                    if unused_access_role_names is not None and role_name:
                        unused_access_role_names.add(role_name)
                    rows.append(new_row(
                        "IAMRoleUnusedAccessFinding", region, role_arn, role_name or role_arn,
                        f.get("createdAt"), f.get("analyzedAt"), days_ago(f.get("analyzedAt")), False,
                        "STALE (ACCESS ANALYZER: CONFIRMED UNUSED)",
                        f"IAM Access Analyzer 'Unused access' finding via analyzer '{analyzer['name']}' - "
                        "AWS's own cross-service unused-role check (authoritative, not a heuristic)",
                    ))
                    raw_capture.record("IAMRoleUnusedAccessFinding", region, role_arn, f)
    except (ClientError, BotoCoreError) as e:
        rows.append(error_row("IAMRoleUnusedAccessFinding", region, "ERROR", str(e)))
    return rows


def _pages(iam, operation, service, rows, **kwargs):
    """Every page of one IAM list call, or one error row: a denial must never abort the scan."""
    try:
        return list(iam.get_paginator(operation).paginate(**kwargs))
    except (ClientError, BotoCoreError) as e:
        rows.append(error_row(service, "global", "ERROR", str(e)))
        return []


def collect_iam(session, used_role_names=None, unused_access_role_names=None):
    """IAM (global). used_role_names: roles seen attached elsewhere (a hint only);
    unused_access_role_names: Access Analyzer's verdict (authoritative)."""
    iam = session.client("iam", config=RETRY_CONFIG)
    rows = []

    # Users + access keys
    for page in _pages(iam, "list_users", "IAMUser", rows):
        for user in page.get("Users", []):
            uname = user["UserName"]
            created = user.get("CreateDate")
            candidates = []
            pw_last_used = user.get("PasswordLastUsed")
            if pw_last_used:
                candidates.append(pw_last_used)
            keys_resp = safe_call(iam.list_access_keys, UserName=uname)
            if "__error__" not in keys_resp:
                for key in keys_resp.get("AccessKeyMetadata", []):
                    detail = safe_call(iam.get_access_key_last_used, AccessKeyId=key["AccessKeyId"])
                    if "__error__" not in detail:
                        lu = detail.get("AccessKeyLastUsed", {}).get("LastUsedDate")
                        if lu:
                            candidates.append(lu)
            best_dt = max(candidates) if candidates else None
            best_days = days_ago(best_dt)
            rows.append(new_row(
                "IAMUser", "global", uname, uname,
                created, best_dt, best_days, best_dt is None,
                flag_stale(best_days, days_ago(created), best_dt is None),
                "Based on password-last-used + access-key-last-used (native, reliable, long-window IAM data)",
                arn=user.get("Arn", ""),
            ))
            raw_capture.record("IAMUser", "global", uname, user)

    # Groups
    for page in _pages(iam, "list_groups", "IAMGroup", rows):
        for group in page.get("Groups", []):
            gname = group["GroupName"]
            created = group.get("CreateDate")
            members_resp = safe_call(iam.get_group, GroupName=gname)
            member_count = len(members_resp.get("Users", [])) if "__error__" not in members_resp else None
            rows.append(new_row(
                "IAMGroup", "global", gname, gname,
                created, "", None, True,
                "STALE (EMPTY GROUP)" if member_count == 0 else flag_stale(None, days_ago(created), True),
                "IAM groups have no native last-used signal; flag is based on member count / creation date only",
                arn=group.get("Arn", ""),
            ))
            raw_capture.record("IAMGroup", "global", gname, group)

    # Customer-managed policies (AWS-managed policies excluded via Scope='Local')
    for page in _pages(iam, "list_policies", "IAMPolicy", rows, Scope="Local"):
        for policy in page.get("Policies", []):
            pname = policy["PolicyName"]
            created = policy.get("CreateDate")
            updated = policy.get("UpdateDate")
            attachment_count = policy.get("AttachmentCount", 0)
            ref_days = days_ago(updated) if updated else days_ago(created)
            notes = ("Customer-managed policies only; UpdateDate is last edit to the policy document, "
                     "not last actual use of the permissions it grants.")

            row = new_row(
                "IAMPolicy", "global", policy.get("Arn", pname), pname,
                created, updated, ref_days, True,
                "STALE (UNATTACHED)" if attachment_count == 0 else flag_stale(ref_days, None, True),
                notes,
                arn=policy.get("Arn", ""),
            )
            raw_capture.record("IAMPolicy", "global", policy.get("Arn", pname), policy)

            # Name what the policy is attached to, not just a count.
            if attachment_count and policy.get("Arn"):
                entity_names = []
                try:
                    for epage in iam.get_paginator("list_entities_for_policy").paginate(PolicyArn=policy["Arn"]):
                        for r_ in epage.get("PolicyRoles", []):
                            add_edge(row, r_["RoleName"], "attached to role",
                                     "IAM list_entities_for_policy",
                                     conn_type="iampolicy.iamrole.attachment", target_service="IAMRole")
                            entity_names.append(f"role {r_['RoleName']}")
                        for u_ in epage.get("PolicyUsers", []):
                            add_edge(row, u_["UserName"], "attached to user",
                                     "IAM list_entities_for_policy",
                                     conn_type="iampolicy.iamuser.attachment", target_service="IAMUser")
                            entity_names.append(f"user {u_['UserName']}")
                        for g_ in epage.get("PolicyGroups", []):
                            add_edge(row, g_["GroupName"], "attached to group",
                                     "IAM list_entities_for_policy",
                                     conn_type="iampolicy.iamgroup.attachment", target_service="IAMGroup")
                            entity_names.append(f"group {g_['GroupName']}")
                except (ClientError, BotoCoreError) as e:
                    logger.warning("listing entities for policy %s failed, "
                                   "attachments under-reported: %s",
                                   row.get("resource_id", "?"), e)
                    row["notes"] += f" Could not list attached entities ({e}) - attachments under-reported."
                if not entity_names:
                    row["notes"] += (f" AWS reports AttachmentCount={attachment_count} but returned no "
                                     "entities - it may be attached only as a permissions boundary.")

            # The resources this policy itself grants access to, same treatment
            # as a role's inline policies (see gather_role_policy_grants).
            if policy.get("Arn") and policy.get("DefaultVersionId"):
                ver = safe_call(iam.get_policy_version, PolicyArn=policy["Arn"],
                                VersionId=policy["DefaultVersionId"])
                if "__error__" not in ver:
                    for arn in extract_resource_arns(parse_policy_document(ver["PolicyVersion"]["Document"])):
                        grant_id, grant_service = probable_resource_id_from_arn(arn)
                        if grant_id:
                            grant_account, grant_region = arn_scope(arn)
                            add_edge(row, grant_id, "grants access to",
                                     "Resource ARN in this policy document",
                                     conn_type="iampolicy.any.resource-grant",
                                     assert_exists=False, target_service=grant_service,
                                     target_arn=arn, target_account=grant_account,
                                     target_region=grant_region)
                            continue
                        api_id = apigateway_id_from_execute_api_arn(arn)
                        if api_id:
                            # target_service is deliberately omitted - see
                            # apigateway_id_from_execute_api_arn.
                            add_edge(row, api_id, "grants API access to",
                                     "execute-api Resource ARN in this policy document",
                                     conn_type="iampolicy.apigateway.execute-api-grant",
                                     assert_exists=False)
                            continue
                        prefix, prefix_service = resource_id_prefix_from_arn(arn)
                        prefix_account, prefix_region = arn_scope(arn)
                        add_edge(row, prefix, "grants access to",
                                 f"prefix-wildcard Resource ARN '{arn}' in this policy document",
                                 conn_type="iampolicy.any.resource-grant-prefix",
                                 assert_exists=False, target_match="prefix",
                                 target_service=prefix_service,
                                 target_account=prefix_account,
                                 target_region=prefix_region)
            rows.append(row)

    # Roles
    for page in _pages(iam, "list_roles", "IAMRole", rows):
        for role in page.get("Roles", []):
            rname = role["RoleName"]
            path = role.get("Path", "/")
            created = role.get("CreateDate")
            last_used = role.get("RoleLastUsed", {}).get("LastUsedDate")
            if last_used is None:
                # ListRoles does not reliably include RoleLastUsed; GetRole does.
                detail = safe_call(iam.get_role, RoleName=rname)
                if "__error__" not in detail:
                    last_used = detail.get("Role", {}).get("RoleLastUsed", {}).get("LastUsedDate")
            ref_days = days_ago(last_used) if last_used else None
            base_flag = flag_stale(ref_days, days_ago(created), last_used is None)

            confirmed_unused = bool(unused_access_role_names) and rname in unused_access_role_names
            has_scanned_consumer = used_role_names is not None and rname in used_role_names
            service_linked = path.startswith("/aws-service-role/")

            # Who can assume the role and what its own policies grant, read from its documents.
            # Trusted services also name roles used by services this tool does not scan.
            trusted_services = extract_trust_service_principals(
                parse_policy_document(role.get("AssumeRolePolicyDocument")))
            resource_grants = gather_role_policy_grants(iam, rname)
            unscanned_trusted_services = trusted_services - {"lambda.amazonaws.com", "ec2.amazonaws.com"}

            notes = "RoleLastUsed is native IAM data (reliable, long-window)."
            connections_parts = []
            if confirmed_unused:
                flag = "STALE (ACCESS ANALYZER: CONFIRMED UNUSED)"
                notes += " IAM Access Analyzer independently flagged this role as unused account-wide - the strongest signal available."
            elif used_role_names is None:
                flag = base_flag
            elif service_linked:
                flag = base_flag
                notes += " AWS service-linked role (path under /aws-service-role/) - not cross-checked against scanned resources."
            elif has_scanned_consumer:
                flag = base_flag
                notes += " Currently attached to a Lambda function or EC2 instance profile scanned in this run."
                connections_parts.append("used by a scanned EC2 instance profile or Lambda function")
            elif unscanned_trusted_services:
                flag = base_flag
                notes += (f" Trust policy scopes this role to {', '.join(sorted(unscanned_trusted_services))}, "
                          "which this script doesn't scan - usage can't be confirmed or ruled out from this data.")
            elif base_flag.startswith(("STALE", "UNKNOWN")):
                flag = base_flag + " + NO SCANNED CONSUMER"
                notes += (" Not found as a Lambda execution role or EC2 instance-profile role in this scan, and "
                          "its trust policy doesn't name another AWS service either - "
                          "NOT conclusive proof it's orphaned (roles are also used by ECS, Step Functions, CodeBuild, "
                          "EventBridge, cross-account trust, etc. which this script doesn't scan). Verify before deleting.")
            else:
                flag = base_flag

            unmatched_grants = []
            if resource_grants:
                notes += (f" Own policies (inline + customer-managed only, AWS-managed policies excluded as "
                          f"too broad) grant access to {len(resource_grants)} specific resource ARN(s).")

            row = new_row(
                "IAMRole", "global", rname, rname,
                created, last_used, ref_days, last_used is None,
                flag, notes, description=role.get("Description", ""),
                connections=join_nonempty(connections_parts),
                iam_role=rname,
                arn=role.get("Arn", ""),
            )
            raw_capture.record("IAMRole", "global", rname, role)
            for arn in sorted(resource_grants):
                rid, grant_service = probable_resource_id_from_arn(arn)
                if rid:
                    # Policies often name other accounts or unscanned resources: no DANGLING.
                    grant_account, grant_region = arn_scope(arn)
                    add_edge(row, rid, "IAM policy grants access to",
                             "IAM policy Resource ARN on this role",
                             conn_type="iamrole.any.resource-grant",
                             assert_exists=False, target_service=grant_service,
                             target_arn=arn, target_account=grant_account,
                             target_region=grant_region)
                    continue
                api_id = apigateway_id_from_execute_api_arn(arn)
                # Gated, so when off the grant still shows in unmatched_grants.
                if api_id and detection_enabled("iamrole.apigateway.execute-api-grant"):
                    # target_service is deliberately omitted - see
                    # apigateway_id_from_execute_api_arn.
                    add_edge(row, api_id, "IAM policy grants API access to",
                             f"execute-api Resource ARN '{arn}' on this role",
                             conn_type="iamrole.apigateway.execute-api-grant",
                             assert_exists=False)
                    continue
                prefix, prefix_service = resource_id_prefix_from_arn(arn)
                # Gated, so when off the wildcard grant is listed as unmatched, not lost.
                if prefix and detection_enabled("iamrole.any.resource-grant-prefix"):
                    prefix_account, prefix_region = arn_scope(arn)
                    add_edge(row, prefix, "IAM policy grants access to",
                             f"prefix-wildcard IAM policy Resource ARN '{arn}' on this role",
                             conn_type="iamrole.any.resource-grant-prefix",
                             assert_exists=False, target_match="prefix",
                             target_service=prefix_service,
                             target_account=prefix_account,
                             target_region=prefix_region)
                else:
                    unmatched_grants.append(arn)
            if unmatched_grants:
                shown = unmatched_grants[:3]
                more = f" (+{len(unmatched_grants) - 3} more)" if len(unmatched_grants) > 3 else ""
                row["connections"] = join_nonempty([
                    row["connections"],
                    f"IAM policy also grants access to (not matched to a scanned resource): "
                    f"{', '.join(shown)}{more}",
                ], sep=" | ")
            rows.append(row)
    return rows
