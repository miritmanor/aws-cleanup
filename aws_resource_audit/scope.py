"""Who is being scanned: the account and partition every row belongs to, set once by
run(). Tests use scan_scope_of() rather than assigning, so scope cannot leak."""

from contextlib import contextmanager

# Fallback partition for a scan that never set one; real scans read it from their ARN.
DEFAULT_PARTITION = "aws"

# Empty rather than None: it goes straight into a resource key, and "" makes an
# unknown account visible in the key instead of rendering as the word "None".
_account = ""
_partition = DEFAULT_PARTITION


def set_scan_scope(account="", partition=DEFAULT_PARTITION):
    """Install the identity for the rest of the run. Called by run()."""
    global _account, _partition
    _account = account or ""
    _partition = partition or DEFAULT_PARTITION


def scan_account():
    return _account


def scan_partition():
    return _partition


@contextmanager
def scan_scope_of(account, partition=DEFAULT_PARTITION):
    """Temporarily scan as this identity. For tests and for nothing else."""
    previous = (_account, _partition)
    set_scan_scope(account, partition)
    try:
        yield
    finally:
        set_scan_scope(*previous)
