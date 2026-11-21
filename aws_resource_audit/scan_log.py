"""The scan's console output, also written to a file as it happens. A tee over whatever
console sink is already set, so the web app's log tail keeps working unchanged."""

import logging
import traceback

from . import console
from .errors import AuditError, ScanCancelled

logger = logging.getLogger(__name__)


class ScanLog:
    """Context manager: every console line goes to the previous sink and to `path`.
    An exception's text is written last; a file that fails never stops the scan."""

    def __init__(self, path):
        self.path = path
        self._file = None
        self._previous = None

    def __enter__(self):
        try:
            # Line-buffered, so a scan still running can be read from the file.
            self._file = open(self.path, "w", encoding="utf-8", buffering=1)  # pylint: disable=consider-using-with
        except OSError as e:
            logger.warning("could not open the scan log %s (%s); the scan "
                           "continues without it", self.path, e)
        self._previous = console.set_sink(self._tee)
        return self

    def _write(self, text):
        if self._file is None:
            return
        try:
            self._file.write(text)
        except (OSError, ValueError) as e:
            logger.warning("writing the scan log %s failed (%s); it stops here",
                           self.path, e)
            self._close()

    def _tee(self, message, stream, end, flush):
        self._previous(message, stream, end, flush)
        self._write(f"{message}{end}")

    def _close(self):
        if self._file is not None:
            try:
                self._file.close()
            except OSError as e:
                logger.warning("closing the scan log %s failed (%s)", self.path, e)
            self._file = None

    def __exit__(self, exc_type, exc, tb):
        console.set_sink(self._previous)
        if exc is not None:
            # Expected stops are their message; anything else is a bug and keeps its traceback.
            if isinstance(exc, (AuditError, ScanCancelled)):
                self._write(f"\n{exc}\n")
            else:
                self._write("\n" + "".join(traceback.format_exception(exc_type, exc, tb)))
        self._close()
        return False
