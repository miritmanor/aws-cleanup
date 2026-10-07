"""Authenticating to AWS: GetCallerIdentity on the profile, and optionally AssumeRole
into settings.role_name in the same account."""

import boto3
from botocore.exceptions import BotoCoreError

from .calls import safe_call
from .credentials import credentials_help
from ..config import RETRY_CONFIG
from ..errors import AuditError
from ..settings import resolved_role_name

ROLE_SESSION_NAME = "aws-resource-audit"


def _auth_failed(message, profile):
    """One wording for every authentication failure: AWS's message unedited, then
    credentials advice when it applies."""
    message = f"Could not authenticate to AWS: {message}"
    help_text = credentials_help(profile, message)
    return f"{message}\n\n{help_text}" if help_text else message


def _session(profile=None, **kwargs):
    """boto3.Session(...), with a bad profile turned into an AuditError."""
    try:
        return boto3.Session(**kwargs)
    except BotoCoreError as e:
        raise AuditError(_auth_failed(e, profile))


def _sts(session):
    return session.client("sts", region_name=session.region_name or "us-east-1",
                          config=RETRY_CONFIG)


def _caller_identity(session, profile=None):
    ident = safe_call(_sts(session).get_caller_identity)
    if "__error__" in ident:
        raise AuditError(_auth_failed(ident["__error__"], profile))
    return ident


def authenticated_session(args, settings):
    """The session and identity run() scans with: the profile, optionally traded for
    the audit role. Raises AuditError on failure."""
    profile = getattr(args, "profile", None)
    base_session = _session(profile, profile_name=profile) if profile else _session()
    base_ident = _caller_identity(base_session, profile)

    if not settings.assume_role:
        return base_session, base_ident

    role_name = resolved_role_name(settings)
    role_arn = f"arn:aws:iam::{base_ident['Account']}:role/{role_name}"
    assumed = safe_call(_sts(base_session).assume_role,
                        RoleArn=role_arn, RoleSessionName=ROLE_SESSION_NAME)
    if "__error__" in assumed:
        raise AuditError(f"Could not assume role {role_arn}: {assumed['__error__']}")

    creds = assumed["Credentials"]
    session = _session(
        profile,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=base_session.region_name,
    )
    return session, _caller_identity(session, profile)
