"""A session on the audit role whose credentials renew themselves: AssumeRole once now,
and again from the base identity whenever botocore says they are about to expire."""

import logging
from datetime import datetime, timezone

import boto3
import botocore.session
from botocore.credentials import CredentialProvider, RefreshableCredentials
from botocore.exceptions import BotoCoreError, ClientError

from ..config import RETRY_CONFIG
from ..errors import AuditError
from .auth_guard import RoleRefreshFailed
from .calls import safe_call

logger = logging.getLogger(__name__)

ROLE_SESSION_NAME = "aws-resource-audit"
REFRESH_METHOD = "assume-role-refreshing"


class _FixedProvider(CredentialProvider):
    """Hands botocore one prepared credentials object, ahead of its own chain."""

    METHOD = REFRESH_METHOD
    CANONICAL_NAME = "custom-assume-role-refreshing"

    def __init__(self, credentials):
        super().__init__()
        self._credentials = credentials

    def load(self):
        return self._credentials


def _expiry(value):
    """AssumeRole's Expiration, as the string RefreshableCredentials parses."""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _fetcher(base_session, role_arn):
    sts = base_session.client("sts", region_name=base_session.region_name or "us-east-1",
                              config=RETRY_CONFIG)

    def fetch():
        creds = sts.assume_role(RoleArn=role_arn,
                                RoleSessionName=ROLE_SESSION_NAME)["Credentials"]
        return {"access_key": creds["AccessKeyId"], "secret_key": creds["SecretAccessKey"],
                "token": creds["SessionToken"], "expiry_time": _expiry(creds["Expiration"])}
    return fetch


def _already_expired(metadata):
    """True when renewed credentials end before this machine's clock says now."""
    expiry = metadata["expiry_time"]
    end = datetime.fromisoformat(expiry) if isinstance(expiry, str) else expiry
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return end <= datetime.now(timezone.utc)


def _refresher(fetch, role_arn):
    # botocore raises a bare RuntimeError for credentials that end in the past;
    # raised here as RoleRefreshFailed it stops the scan cleanly instead of crashing it.
    def refresh():
        try:
            metadata = fetch()
            if _already_expired(metadata):
                raise ValueError(f"the new credentials end at {metadata['expiry_time']}, "
                                 "before this computer's clock says now")
            return metadata
        except (ClientError, BotoCoreError, KeyError, ValueError) as e:
            logger.warning("renewing credentials for %s failed: %s", role_arn, e)
            raise RoleRefreshFailed(error=str(e)) from e
    return refresh


def assumed_role_session(base_session, role_arn):
    """A boto3 Session on `role_arn` that renews itself. Raises AuditError if the
    first AssumeRole fails."""
    fetch = _fetcher(base_session, role_arn)
    first = safe_call(fetch)
    if "__error__" in first:
        raise AuditError(f"Could not assume role {role_arn}: {first['__error__']}")
    credentials = RefreshableCredentials.create_from_metadata(
        first, refresh_using=_refresher(fetch, role_arn), method=REFRESH_METHOD)
    core = botocore.session.Session()
    core.get_component("credential_provider").insert_before(
        "env", _FixedProvider(credentials))
    return boto3.Session(botocore_session=core, region_name=base_session.region_name)
