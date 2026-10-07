"""Writing project names to AWS as real tags: the only part that mutates your account.
Dry run unless --confirm; only NAMED projects are tagged."""

from ..config import RETRY_CONFIG
from ..collect.calls import safe_call
from ..console import say


# Opt-in tagging (--tag-groups): the only write; a dry run unless --confirm.

def looks_like_arn(s):
    return bool(s) and s.startswith("arn:")


def build_resource_arn(row, account_id):
    """Best-effort ARN for the Resource Groups Tagging API. resource_id is used as-is
    when it is an ARN; None where one cannot be derived reliably."""
    svc, region, rid = row["service"], row.get("region", ""), row["resource_id"]
    if looks_like_arn(rid):
        return rid
    if not account_id:
        return None
    if svc == "EC2Instance":
        return f"arn:aws:ec2:{region}:{account_id}:instance/{rid}"
    if svc == "EBSVolume":
        return f"arn:aws:ec2:{region}:{account_id}:volume/{rid}"
    if svc == "ElasticIP":
        return f"arn:aws:ec2:{region}:{account_id}:elastic-ip/{rid}"
    if svc == "RDSInstance":
        return f"arn:aws:rds:{region}:{account_id}:db:{rid}"
    if svc == "LambdaFunction":
        return f"arn:aws:lambda:{region}:{account_id}:function:{rid}"
    if svc == "DynamoDBTable":
        return f"arn:aws:dynamodb:{region}:{account_id}:table/{rid}"
    if svc == "S3Bucket":
        return f"arn:aws:s3:::{rid}"
    if svc == "CloudWatchLogGroup":
        return f"arn:aws:logs:{region}:{account_id}:log-group:{rid}"
    if svc == "IAMRole":
        return f"arn:aws:iam::{account_id}:role/{rid}"
    if svc == "IAMUser":
        return f"arn:aws:iam::{account_id}:user/{rid}"
    if svc == "APIGatewayRestApi":
        return f"arn:aws:apigateway:{region}::/restapis/{rid}"
    if svc == "APIGatewayV2Api":
        return f"arn:aws:apigateway:{region}::/apis/{rid}"
    if svc == "AmplifyApp":
        return f"arn:aws:amplify:{region}:{account_id}:apps/{rid}"
    if svc == "CognitoUserPool":
        return f"arn:aws:cognito-idp:{region}:{account_id}:userpool/{rid}"
    return None


def apply_group_tagging(all_rows, session, account_id, confirm):
    """Tag every resource of a named project with Project=<name> (needs tag:TagResources).
    Always prints the plan; calls AWS only when `confirm` is True."""
    named_rows = [r for r in all_rows if r.get("grouping_method") == "named" and r.get("project_group")]
    if not named_rows:
        say("  --tag-groups: no project has an assigned name yet, nothing to tag")
        return

    by_name = {}
    skipped = []
    for r in named_rows:
        arn = build_resource_arn(r, account_id)
        if arn:
            by_name.setdefault(r["project_group"], []).append((r, arn))
        else:
            skipped.append(r)

    tagging_client = session.client("resourcegroupstaggingapi", region_name="us-east-1", config=RETRY_CONFIG) if confirm else None
    total_tagged, total_failed = 0, 0

    for name, pairs in sorted(by_name.items()):
        arns = [arn for _, arn in pairs]
        say(f"  {'Tagging' if confirm else '[DRY RUN] Would tag'} {len(arns)} resource(s) with Project={name}:")
        for r, arn in pairs:
            old = (r.get("tags") or {}).get("Project")
            replaces = f" (replaces Project={old})" if old and old != name else ""
            say(f"      {r['service']} {r['resource_id']} -> {arn}{replaces}")
        if not confirm:
            continue
        for i in range(0, len(arns), 20):  # tag_resources caps at 20 ARNs per call
            batch = arns[i:i + 20]
            resp = safe_call(tagging_client.tag_resources, ResourceARNList=batch, Tags={"Project": name})
            if "__error__" in resp:
                say(f"      ERROR tagging batch: {resp['__error__']}")
                total_failed += len(batch)
                continue
            failed = resp.get("FailedResourcesMap", {})
            total_failed += len(failed)
            total_tagged += len(batch) - len(failed)
            for arn, info in failed.items():
                say(f"      FAILED: {arn} - {info.get('ErrorMessage', info.get('ErrorCode'))}")

    if skipped:
        say(f"  {len(skipped)} skipped - no reliable ARN could be derived (tag those manually)")

    if confirm:
        say(f"  Tagging done: {total_tagged} tagged, {total_failed} failed.")
    else:
        say("  This was a DRY RUN - nothing was written to AWS. Re-run with --tag-groups --confirm to apply these tags for real.")
