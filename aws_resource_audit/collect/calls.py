"""Calling AWS and reading its response shapes: safe_call, paged, error text,
[{Key, Value}] tags and the timestamps botocore leaves as strings."""

import logging
import re
from datetime import datetime, timezone

from botocore.exceptions import BotoCoreError, ClientError

from .. import coverage

logger = logging.getLogger(__name__)

_ERROR_CODE = re.compile(r"\((\w+)\) when calling the")
_DENIED_ACTION = re.compile(r"not authorized to perform:?\s*([\w-]+:[\w*]+)")
_OPERATION = re.compile(r"when calling the (\w+) operation")


def brief_error(err):
    """botocore's error cut down to one line: the AWS error code and the refused action."""
    text = str(err)
    code = _ERROR_CODE.search(text)
    action = _DENIED_ACTION.search(text) or _OPERATION.search(text)
    if code and action:
        return f"{code.group(1)}: {action.group(1)}"
    if code:
        return code.group(1)
    return text.split("\n")[0][:60]


def error_code(err):
    """AWS's own error code ("AccessDenied", "ThrottlingException"), or the
    botocore class name for an error that never reached AWS."""
    if isinstance(err, ClientError):
        return (err.response.get("Error") or {}).get("Code", "")
    return type(err).__name__


class CallFailed(dict):
    """What safe_call returns when AWS refuses: still {"__error__": text}, so
    every existing check works, plus the code and what kind of failure it was."""

    def __init__(self, err, operation=""):
        super().__init__({"__error__": str(err)})
        self.message = str(err)
        self.code = error_code(err)
        self.operation = operation
        self.kind = coverage.classify_error(self.message, self.code)


def safe_call(fn, *args, capability=coverage.CONFIGURATION, **kwargs):
    """Call an AWS API, swallowing permission and availability errors into
    {"__error__": ...}. Logs at DEBUG and records the failure in the coverage ledger."""
    try:
        return fn(*args, **kwargs)
    except (ClientError, BotoCoreError) as e:
        logger.debug("%s failed: %s", getattr(fn, "__name__", fn), e,
                     exc_info=True)
        failed = CallFailed(e, getattr(fn, "__name__", ""))
        coverage.record(capability, failed.kind, operation=failed.operation, error=failed.message)
        return failed


def paged(client, operation, key, *, capability=coverage.INVENTORY,
          service=None, **kwargs):
    """Every item from a paginated operation, as (items, error). A failure part-way
    keeps the pages read and reports PARTIAL."""
    items = []
    try:
        if client.can_paginate(operation):
            for page in client.get_paginator(operation).paginate(**kwargs):
                items.extend(_dig(page, key))
        else:
            # Some list operations (e.g. events:ListEventBuses) have no paginator
            # but still page by NextToken, so page by hand.
            call = getattr(client, operation)
            token, token_key = None, "NextToken"
            while True:
                page = call(**(dict(kwargs, **{token_key: token}) if token else kwargs))
                items.extend(_dig(page, key))
                # Most APIs page with NextToken; WAFv2 uses NextMarker.
                token_key = "NextMarker" if page.get("NextMarker") else "NextToken"
                token = page.get(token_key)
                if not token:
                    break
    except (ClientError, BotoCoreError) as e:
        message = str(e)
        logger.debug("%s failed after %d item(s): %s", operation, len(items), e,
                     exc_info=True)
        coverage.record(
            capability,
            coverage.PARTIAL if items else coverage.classify_error(message, error_code(e)),
            operation=operation, service=service, error=message, count=len(items))
        return items, message
    coverage.record(capability, coverage.COMPLETE, operation=operation,
                    service=service, count=len(items))
    return items, None


def _dig(page, key):
    """page[key], where a dotted key ("DistributionList.Items") reaches a nested list."""
    for part in key.split("."):
        page = (page or {}).get(part)
    return page or []


def tags_to_dict(tag_list, key_field="Key", val_field="Value"):
    if not tag_list:
        return {}
    out = {}
    for t in tag_list:
        k = t.get(key_field)
        if k:
            out[k] = t.get(val_field, "")
    return out

def parse_aws_ts_string(value):
    """Parse AWS fields typed as strings rather than timestamps (Lambda LastModified,
    AMI CreationDate, ...) so every date shares one format."""
    if not value:
        return None
    try:
        return datetime.strptime(
            value.replace("Z", "").split(".")[0], "%Y-%m-%dT%H:%M:%S"
        ).replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError, TypeError):
        logger.debug("unparseable AWS timestamp string: %r", value)
        return None


def epoch_seconds_to_dt(value):
    """SQS epoch-second strings -> datetime; None on anything unparseable."""
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (ValueError, TypeError, OSError, OverflowError):
        logger.debug("unparseable epoch-second value: %r", value)
        return None
