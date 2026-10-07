"""The library's error types. Raised as Exceptions, not SystemExit, so servers and
threads can catch them; the CLI converts them to SystemExit. Bugs do not belong here."""


class AuditError(Exception):
    """Something the user must fix before the run can continue. The message is
    shown verbatim, so name the file or setting at fault and what to do."""


class ScanCancelled(Exception):
    """should_stop() said to stop; raised before anything is written.
    Not an AuditError: nothing needs fixing."""
