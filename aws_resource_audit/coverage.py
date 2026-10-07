"""What the scan managed to look at: "none found" and "lookup denied" are opposite
facts, recorded here beside the rows. Tests use ledger_of() so no entry leaks."""

from contextlib import contextmanager
from dataclasses import dataclass, field

from .config import now
from .scope import scan_account

# What was asked for. These fail independently: tags can be denied for resources
# that were enumerated fine.
INVENTORY = "inventory"
CONFIGURATION = "configuration"
TAGS = "tags"
METRICS = "metrics"
DEPENDENCIES = "dependencies"

# How it went.
COMPLETE = "complete"            # enumerated to the end
PARTIAL = "partial"              # some pages read, then it failed
DENIED = "denied"                # permissions
THROTTLED = "throttled"          # rate-limited past botocore's retries; a rerun usually works
FAILED = "failed"                # anything else that raised
NOT_REQUESTED = "not_requested"  # this scope was never in the scan
UNSUPPORTED = "unsupported"      # the service does not exist in this scope

# Statuses that mean "we did not see everything here", which is what the
# reference resolver and the risk heuristic actually care about.
INCOMPLETE_STATUSES = frozenset({PARTIAL, DENIED, THROTTLED, FAILED})

# Worst first: when one type has several entries, the first of these present is its status.
WORST_FIRST = (FAILED, THROTTLED, DENIED, PARTIAL, NOT_REQUESTED, UNSUPPORTED)

# What a reader should do about each kind of gap, for the audit file.
NEXT_STEP = {
    DENIED: "grant the action named in the error (iam/aws-audit-role.yaml)",
    THROTTLED: "AWS rate-limited the call; run the scan again",
    PARTIAL: "some pages were read before the error; see the error",
    FAILED: "see the error",
}

_DENIED_MARKERS = (
    "accessdenied", "access denied", "unauthorized", "not authorized",
    "authorizationerror", "forbidden", "optinrequired",
)

_THROTTLED_MARKERS = (
    "throttl", "rate exceeded", "toomanyrequests", "requestlimitexceeded", "slowdown",
    "provisionedthroughputexceeded",
)


# Errors that are an ANSWER ("no tags", "never configured"), not a failure.
_ABSENT_MARKERS = (
    "nosuchtagset", "nosuchlifecycleconfiguration", "nosuchbucketpolicy",
    "nosuchcorsconfiguration", "nosuchwebsiteconfiguration",
)

# AWS switching an operation off for the account, as it did for retired features.
# It arrives as AccessDeniedException, but no IAM grant changes the answer.
_UNSUPPORTED_MARKERS = (
    "operation is currently disabled",
)


def classify_error(message, code=""):
    """COMPLETE (error means "none"), UNSUPPORTED, DENIED, THROTTLED or FAILED.
    The AWS error code decides when available; the message is the fallback."""
    if any(m in (message or "").lower() for m in _UNSUPPORTED_MARKERS):
        return UNSUPPORTED
    for text in ((code or "").lower(), (message or "").lower()):
        if any(m in text for m in _ABSENT_MARKERS):
            return COMPLETE
        if any(m in text for m in _DENIED_MARKERS):
            return DENIED
        if any(m in text for m in _THROTTLED_MARKERS):
            return THROTTLED
    return FAILED


@dataclass(frozen=True)
class CoverageEntry:
    """One capability, in one scope, and how it went."""

    account: str
    scope: str          # region name, or "global"
    collector: str      # the scan() label, so it reads as the console line did
    service: str        # resource type, "" when the entry is collector-wide
    capability: str
    operation: str      # the AWS API call, so a denial names what to grant
    status: str
    scanned_at: object
    error: str = ""
    count: int = 0

    def as_dict(self):
        data = self.__dict__.copy()
        stamp = data.get("scanned_at")
        data["scanned_at"] = stamp.isoformat() if hasattr(stamp, "isoformat") else ""
        return data


@dataclass
class Ledger:
    """Every coverage entry from one scan, plus the lookups over it."""

    entries: list = field(default_factory=list)
    # The collector running now, (collector, scope, service), set by collect_all.
    context: tuple = ("", "", "")

    def record(self, capability, status, *, operation="", service=None,
               scope=None, collector=None, account="", error="", count=0):
        collector_name, ctx_scope, ctx_service = self.context
        self.entries.append(CoverageEntry(
            # From the scan identity, like every row's account: a ledger read
            # back later has to say whose account it describes.
            account=account or scan_account(),
            scope=scope if scope is not None else ctx_scope,
            collector=collector if collector is not None else collector_name,
            service=service if service is not None else ctx_service,
            capability=capability, operation=operation, status=status,
            scanned_at=now(), error=error, count=count,
        ))

    def status(self, service, scope=None, capability=INVENTORY):
        """How well `service` was covered in `scope`; worst status wins. None means
        the ledger has nothing to say (e.g. an old snapshot), not NOT_REQUESTED."""
        seen = [e for e in self.entries
                if e.service == service and e.capability == capability
                and (scope is None or e.scope == scope)]
        if not seen:
            return None
        for status in WORST_FIRST:
            if any(e.status == status for e in seen):
                return status
        return COMPLETE

    def scopes_for(self, service, capability=INVENTORY):
        """Scopes this service was enumerated in; "global" alone means one listing
        covered all of it."""
        return {e.scope for e in self.entries
                if e.service == service and e.capability == capability}

    def scopes_requested(self):
        return {e.scope for e in self.entries if e.status != NOT_REQUESTED}

    def as_dicts(self):
        return [e.as_dict() for e in self.entries]

    def gaps(self):
        """Every entry that is not COMPLETE, worst status first."""
        rank = {s: i for i, s in enumerate(WORST_FIRST)}
        return sorted((e for e in self.entries if e.status != COMPLETE),
                      key=lambda e: (rank.get(e.status, len(WORST_FIRST)),
                                     e.scope, e.service, e.operation))

    def counts(self):
        """Entries per status, for the one-line scan summary."""
        out = {}
        for entry in self.entries:
            out[entry.status] = out.get(entry.status, 0) + 1
        return out


_ledger = Ledger()


def ledger():
    return _ledger


def record(*args, **kwargs):
    return _ledger.record(*args, **kwargs)


@contextmanager
def collecting(collector, scope, service=""):
    """Name what is running, so safe_call can attribute a failure to it."""
    previous = _ledger.context
    _ledger.context = (collector, scope, service)
    try:
        yield
    finally:
        _ledger.context = previous


@contextmanager
def ledger_of(new_ledger=None):
    """Swap the ledger wholesale. For tests, and for a second scan in one
    process - the web app runs one per request and must not accumulate."""
    global _ledger
    previous = _ledger
    _ledger = new_ledger if new_ledger is not None else Ledger()
    try:
        yield _ledger
    finally:
        _ledger = previous


def reset():
    """Start a fresh ledger in place. Called by run() before a scan."""
    global _ledger
    _ledger = Ledger()
    return _ledger
