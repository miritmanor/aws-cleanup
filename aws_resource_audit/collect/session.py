"""Authenticating to AWS: GetCallerIdentity on the profile, and optionally AssumeRole
into settings.role_name in the same account, with credentials that renew themselves."""

import boto3
from botocore.exceptions import BotoCoreError

from .calls import safe_call
from .credentials import credentials_help
from .role_credentials import assumed_role_session
from ..config import RETRY_CONFIG
from ..errors import AuditError
from ..settings import resolved_role_name


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
        raise AuditError(_auth_failed(e, profile)) from e


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
    session = assumed_role_session(base_session, role_arn)
    return session, _caller_identity(session, profile)
