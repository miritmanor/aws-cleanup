"""KMS keys, Secrets Manager secrets and advanced SSM parameters: small monthly charges
each ($1 a key, $0.40 a secret, $0.05 a parameter), "forgot to delete" clutter in aggregate."""

from ... import coverage
from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.arns import probable_resource_id_from_arn
from ...collect.calls import paged, safe_call, tags_to_dict
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago, flag_stale
from ...text import join_nonempty

_KEY_FLAGS = {
    "PendingDeletion": "STALE (pending deletion)",
    "Disabled": "STALE (disabled - still billed $1/month)",
}


def _aliases(kms):
    aliases, _error = paged(kms, "list_aliases", "Aliases", service="KMSKey")
    out = {}
    for alias in aliases:
        if alias.get("TargetKeyId"):
            out.setdefault(alias["TargetKeyId"], alias.get("AliasName", ""))
    return out


def collect_kms_keys(session, region):
    """Customer-managed keys only: AWS-managed keys are free, and listing them
    would be noise. No usage signal short of CloudTrail, so enabled is UNKNOWN."""
    kms = session.client("kms", region_name=region, config=RETRY_CONFIG)
    keys, page_error = paged(kms, "list_keys", "Keys", service="KMSKey")
    if page_error and not keys:
        return [error_row("KMSKey", region, "ERROR", page_error)]
    aliases = _aliases(kms)
    rows = []
    for key in keys:
        detail = safe_call(kms.describe_key, KeyId=key["KeyId"])
        meta = detail.get("KeyMetadata") if "__error__" not in detail else None
        if not meta or meta.get("KeyManager") != "CUSTOMER":
            continue
        kid = meta["KeyId"]
        state = meta.get("KeyState", "")
        tags_resp = safe_call(kms.list_resource_tags, capability=coverage.TAGS, KeyId=kid)
        tags = {} if "__error__" in tags_resp else {
            t["TagKey"]: t["TagValue"] for t in tags_resp.get("Tags", [])}
        created = meta.get("CreationDate")
        row = new_row(
            "KMSKey", region, kid, aliases.get(kid, ""), created, None, days_ago(created), True,
            _KEY_FLAGS.get(state, "UNKNOWN (key use is only visible in CloudTrail)"),
            "A customer-managed key costs about $1/month while enabled or disabled, until its "
            "deletion completes. Deleting one makes everything encrypted with it unreadable - "
            "check what uses it first. ", tags=tags,
            description=join_nonempty([state, meta.get("KeySpec", ""), meta.get("Description", "")], ", "),
            arn=meta.get("Arn", ""))
        raw_capture.record("KMSKey", region, kid, meta)
        rows.append(row)
    return rows


def _key_id(value):
    """A key id from an ARN or a bare id; None for an alias, which names no collected key."""
    if not value or value.startswith("alias/"):
        return None
    if value.startswith("arn:"):
        return probable_resource_id_from_arn(value)[0]
    return value


def collect_secrets(session, region):
    """LastAccessedDate is a genuine usage signal, to the day - rare and valuable."""
    client = session.client("secretsmanager", region_name=region, config=RETRY_CONFIG)
    secrets, page_error = paged(client, "list_secrets", "SecretList", service="Secret")
    if page_error and not secrets:
        return [error_row("Secret", region, "ERROR", page_error)]
    rows = []
    for secret in secrets:
        name = secret["Name"]
        created = secret.get("CreatedDate")
        accessed = secret.get("LastAccessedDate")
        notes = "About $0.40/month per secret, plus API calls. "
        if accessed is None:
            notes += "Never read since AWS began recording access. "
        if secret.get("RotationEnabled"):
            notes += "Rotation is enabled. "
        row = new_row(
            "Secret", region, name, name, created, accessed,
            days_ago(accessed) if accessed else days_ago(created), accessed is None,
            flag_stale(days_ago(accessed) if accessed else None, days_ago(created), accessed is None),
            notes, tags=tags_to_dict(secret.get("Tags", [])),
            description=(secret.get("Description") or "")[:70], arn=secret.get("ARN", ""))
        raw_capture.record("Secret", region, name, secret)
        add_edge(row, _key_id(secret.get("KmsKeyId")), "is encrypted with key", "secret KmsKeyId",
                 conn_type="secret.kmskey.encryption", target_service="KMSKey")
        rotation_fn, _svc = probable_resource_id_from_arn(secret.get("RotationLambdaARN") or "")
        add_edge(row, rotation_fn, "is rotated by", "secret RotationLambdaARN",
                 conn_type="secret.lambda.rotation", target_service="LambdaFunction")
        rows.append(row)
    return rows


def _key_id(value):
    """A parameter's KeyId as a key id; an alias names no row, so it links nothing."""
    if (value or "").startswith("arn:"):
        return probable_resource_id_from_arn(value)[0]
    return None if not value or value.startswith("alias/") else value


def collect_ssm_parameters(session, region):
    """Advanced-tier parameters only: each bills about $0.05/month. Standard ones
    are free, and Amplify and CDK create dozens of them."""
    client = session.client("ssm", region_name=region, config=RETRY_CONFIG)
    params, page_error = paged(client, "describe_parameters", "Parameters", service="SSMParameter",
                               ParameterFilters=[{"Key": "Tier", "Values": ["Advanced"]}])
    if page_error and not params:
        return [error_row("SSMParameter", region, "ERROR", page_error)]
    rows = []
    for param in params:
        name = param["Name"]
        tags = safe_call(client.list_tags_for_resource, capability=coverage.TAGS,
                         ResourceType="Parameter", ResourceId=name)
        modified = param.get("LastModifiedDate")
        row = new_row(
            "SSMParameter", region, name, name, None, None, None, True,
            "UNKNOWN (advanced tier - billed monthly; AWS records no reads)",
            "An advanced-tier parameter bills about $0.05/month whether or not anything reads "
            "it. Moving it to the standard tier makes it free if it fits in 4 KB. ",
            tags={} if "__error__" in tags else tags_to_dict(tags.get("TagList", [])),
            description=join_nonempty([param.get("Type", ""), f"version {param.get('Version', '?')}",
                                       f"changed {days_ago(modified)}d ago" if modified else "",
                                       (param.get("Description") or "")[:60]], ", "),
            arn=param.get("ARN", ""))
        raw_capture.record("SSMParameter", region, name, param)
        add_edge(row, _key_id(param.get("KeyId")), "encrypted with", "parameter KeyId",
                 conn_type="ssmparameter.kmskey.encryption", target_service="KMSKey")
        rows.append(row)
    return rows
