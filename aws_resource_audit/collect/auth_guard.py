"""Watches every AWS call of a scan through botocore events: retries a clock error once,
and records a credential AWS stopped accepting so the scan can stop before it saves."""

import logging
import threading

from botocore.exceptions import BotoCoreError

from ..errors import AuditError
from .credentials import credentials_help

logger = logging.getLogger(__name__)

# The container clock caught up within about 8 s of a wake; twice that, once per request.
CLOCK_RETRY_SECONDS = 15

CLOCK_CODES = frozenset({"RequestExpired", "RequestTimeTooSkewed", "SignatureExpired"})
CLOCK_MESSAGE = "signature expired"
# Retried once too: a clock that is behind keeps botocore from renewing the token in time.
EXPIRED_CODES = frozenset({"ExpiredToken", "ExpiredTokenException"})

_RETRIED = "audit_auth_retried"


class RoleRefreshFailed(BotoCoreError):
    """Renewing the assumed role's credentials failed. A BotoCoreError, so safe_call
    and scan() treat it as a failed lookup until the guard stops the scan."""

    fmt = "Could not renew the audit role's credentials: {error}"


def _error(response):
    """(code, message) of a failed response, or None for a success or no response."""
    if not response or len(response) < 2:
        return None
    error = (response[1] or {}).get("Error") or {}
    code = error.get("Code") or ""
    if not code:
        return None
    return code, error.get("Message") or ""


def is_clock_error(code, message):
    return code in CLOCK_CODES or CLOCK_MESSAGE in (message or "").lower()


class AuthGuard:
    """The first rejected credential of a scan; check() turns it into an AuditError."""

    def __init__(self):
        self._lock = threading.Lock()
        self.rejection = None

    def install(self, session):
        """Register on the session's events; clients created afterwards inherit them."""
        session.events.register("needs-retry", self.on_needs_retry)
        session.events.register("after-call-error", self.on_call_error)
        return self

    def _record(self, region, operation, code, message, clock):
        with self._lock:
            if self.rejection is None:
                self.rejection = {"region": region or "global", "operation": operation,
                                  "code": code, "message": message, "clock": clock}
        logger.warning("AWS rejected the scan's credentials: %s on %s in %s - %s",
                       code, operation, region or "global", message)

    def on_needs_retry(self, response=None, operation=None, request_dict=None, **_kwargs):
        """A delay in seconds to retry a clock or expired-token error once; None
        otherwise, which leaves every other retry to botocore."""
        found = _error(response)
        if found is None or self.rejection is not None:
            # After a rejection, fail fast: the scan stops at its next checkpoint anyway.
            return None
        code, message = found
        clock = is_clock_error(code, message)
        if not clock and code not in EXPIRED_CODES:
            return None
        context = (request_dict or {}).setdefault("context", {})
        region = context.get("client_region")
        name = getattr(operation, "name", "") or ""
        if context.get(_RETRIED):
            self._record(region, name, code, message, clock)
            return None
        context[_RETRIED] = True
        logger.warning("%s on %s in %s; retrying once in %d s", code, name,
                       region or "global", CLOCK_RETRY_SECONDS)
        return CLOCK_RETRY_SECONDS

    def on_call_error(self, exception=None, context=None, event_name="", **_kwargs):
        """A failed renewal raises while the request is being signed, before any response."""
        if isinstance(exception, RoleRefreshFailed):
            operation = event_name.rsplit(".", 1)[-1] if event_name else ""
            self._record((context or {}).get("client_region"), operation,
                         "RoleRefreshFailed", str(exception), False)

    def check(self, profile=None):
        """Raise AuditError if AWS rejected the scan's credentials; otherwise nothing."""
        with self._lock:
            rejection = self.rejection
        if rejection is None:
            return
        raise AuditError(rejection_message(rejection, profile))


def rejection_message(rejection, profile=None):
    """The whole of what the user sees: what AWS said, that nothing was saved, what to do."""
    where = (f"{rejection['code']} on {rejection['operation']} in {rejection['region']}: "
             f"{rejection['message']}")
    saved = "The scan stopped and no results were saved; the previous results are unchanged."
    if rejection["clock"]:
        return ("Could not authenticate to AWS: this computer's clock is behind AWS's "
                f"clock ({where}).\n\n{saved}\n\nThis happens for a short time after the "
                "computer wakes from sleep. Run the scan again, and keep the computer "
                "awake while it runs.")
    message = (f"Could not authenticate to AWS: the scan's credentials expired or were "
               f"rejected partway through ({where}).\n\n{saved}")
    help_text = credentials_help(profile, rejection["message"] or "expired")
    return f"{message}\n\n{help_text}" if help_text else message
