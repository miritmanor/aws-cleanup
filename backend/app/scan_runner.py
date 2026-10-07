"""One scan at a time, in a thread, progress readable while it runs. The in-memory lock
protects the core's process-global state; cancel is a flag checked before each AWS step."""

import logging
import threading
import traceback
from collections import deque
from datetime import datetime, timezone

from aws_resource_audit import console, run
from aws_resource_audit.errors import AuditError, ScanCancelled

# Log-tail lines kept for the UI: room for a full scan, bounded against runaway output.
LOG_LINES = 800

IDLE, RUNNING, DONE, FAILED = "idle", "running", "done", "failed"
CANCELLED = "cancelled"


logger = logging.getLogger(__name__)


class ScanInProgress(Exception):
    """A scan was requested while one was running. Answered with 409."""


def _now():
    return datetime.now(timezone.utc)


class ScanState:
    """What the status endpoint reports. Written by the scan thread, read by request
    threads, so every access takes the lock."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self.state = IDLE
            self.started_at = None
            self.finished_at = None
            self.phase = None
            self.stage = None
            self.region = None
            self.collector = None
            self.steps_done = 0
            self.steps_total = 0
            self.error = None
            # True from a stop request until the scan ends, so the UI can say so.
            self.stopping = False
            self.log = deque(maxlen=LOG_LINES)
            self.result = None          # {rows, stale, errors} once done

    def begin(self):
        with self._lock:
            self.state = RUNNING
            self.started_at = _now()

    def finish(self, state, error=None, result=None):
        with self._lock:
            self.state = state
            self.stopping = False
            self.finished_at = _now()
            self.error = error
            self.result = result

    def request_stop(self):
        with self._lock:
            if self.state == RUNNING:
                self.stopping = True

    def append_log(self, line):
        with self._lock:
            self.log.append(line)

    def update(self, event):
        """The progress callback handed to run(), one dict per step; absent keys mean
        "unchanged"."""
        with self._lock:
            self.phase = event.get("phase", self.phase)
            if "stage" in event:
                self.stage = event["stage"]
            if "region" in event:
                self.region = event["region"]
            if "collector" in event:
                self.collector = event["collector"]
            if "step" in event:
                self.steps_done = event["step"]
            if "total" in event:
                self.steps_total = event["total"]
            if event.get("phase") == "done":
                self.result = {
                    "rows": event.get("rows"),
                    "stale": event.get("stale"),
                    "errors": event.get("errors"),
                }

    def snapshot(self):
        """A plain-dict copy taken under the lock, never the live deque."""
        with self._lock:
            return {
                "state": self.state,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "phase": self.phase,
                "stage": self.stage,
                "region": self.region,
                "collector": self.collector,
                "steps_done": self.steps_done,
                "steps_total": self.steps_total,
                "error": self.error,
                "stopping": self.stopping,
                "result": self.result,
                "log": list(self.log),
            }


class ScanRunner:
    """Owns the lock, the thread and the state. One instance per process."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self.state = ScanState()

    def is_running(self):
        return self._lock.locked()

    def status(self):
        return self.state.snapshot()

    def start(self, options, settings, on_finish=None):
        """Begin a scan; raises ScanInProgress if one runs. `options` has run()'s seven
        attributes; `settings` is explicit; `on_finish` runs after, success or not."""
        # Acquired on the REQUEST thread and released by the scan thread, so a second
        # POST is refused rather than queued.
        if not self._lock.acquire(blocking=False):
            raise ScanInProgress(
                "A scan is already running. Wait for it to finish - "
                "GET /api/scans/status reports how far along it is.")

        self._stop.clear()
        self.state.reset()
        self.state.begin()
        self._thread = threading.Thread(
            target=self._run, args=(options, settings, on_finish),
            name="aws-scan", daemon=True)
        self._thread.start()
        return self.status()

    def cancel(self):
        """Ask the running scan to stop; returns the status either way."""
        if self.is_running():
            self._stop.set()
            self.state.request_stop()
            logger.info("scan stop requested")
        return self.status()

    def _run(self, options, settings, on_finish):
        previous_sink = console.set_sink(self._sink)
        try:
            run(options, settings=settings, progress=self.state.update,
                should_stop=self._stop.is_set)
            self.state.finish(DONE, result=self.state.snapshot()["result"])
        except ScanCancelled as e:
            # Asked for, so neither a failure nor a traceback.
            self.state.append_log("")
            self.state.append_log(f"{e} The previous scan is unchanged.")
            logger.info("scan stopped on request")
            self.state.finish(CANCELLED)
        except AuditError as e:
            # Expected and actionable: no credentials, unusable config. The
            # message is written to be the whole of what a user sees.
            self.state.append_log(str(e))
            # No traceback, here or in the log tail: this is the user's problem
            # to fix and the message is the whole of it.
            logger.warning("scan failed: %s", e)
            self.state.finish(FAILED, error=str(e))
        except BaseException as e:                              # noqa: BLE001
            # A bug: the traceback goes to the log tail and the state. BaseException, so
            # this thread can never end with the lock held.
            detail = traceback.format_exc()
            self.state.append_log(detail)
            # Also to the log: until now the only copy of this traceback lived
            # in an in-memory deque, so a restart lost the evidence for good.
            logger.error("scan crashed", exc_info=e)
            self.state.finish(FAILED, error=f"{type(e).__name__}: {e}")
        finally:
            console.set_sink(previous_sink)
            # Released before on_finish: a callback that raises must not be able
            # to leave the app permanently convinced a scan is running.
            self._lock.release()
            if on_finish is not None:
                try:
                    on_finish()
                except Exception:                               # noqa: BLE001
                    # Swallowed (the scan finished) but logged: this callback invalidates the cache.
                    logger.exception("scan on_finish callback failed; cached "
                                     "results may be stale until the next scan")

    def _sink(self, message, stream, end, flush):
        """Every line the scan prints: kept for the UI and still printed. end="" fragments
        are reassembled into whole lines."""
        print(message, end=end, file=stream, flush=flush)
        if not message:
            return
        if end == "":
            self._partial = getattr(self, "_partial", "") + message
            return
        line = getattr(self, "_partial", "") + message
        self._partial = ""
        self.state.append_log(line.rstrip())


# One per process, which is the same statement as "one scan at a time".
runner = ScanRunner()
