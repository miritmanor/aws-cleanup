"""Where progress and diagnostics go: say/detail/warn are the UI a person watches,
mirrored into `logging`. set_sink() redirects all three (e.g. to the web log tail)."""

import logging
import sys

logger = logging.getLogger(__name__)

# Set by run() from --debug. Diagnostics are per-resource detail that would
# drown the normal output, so they are off unless asked for.
VERBOSE = False

_QUIET = False

# Fragments of a line written with end="". Module-global like VERBOSE above and
# for the same reason: one scan runs at a time.
_partial = ""


def _flush_partial():
    """Emit a half-written progress line as its own record."""
    global _partial
    line, _partial = _partial.rstrip(), ""
    if line:
        logger.info("%s", line)


def _log(level, message, end):
    """Mirror one console line into logging - one record per LINE: say() fragments
    are joined, and warn()/detail() flush any buffered fragment first."""
    global _partial
    if end == "":
        _partial += message
        return
    if level != logging.INFO:
        _flush_partial()
        line = message.rstrip()
        if line:
            logger.log(level, "%s", line)
        return
    line = (_partial + message).rstrip()
    _partial = ""
    if line:
        logger.info("%s", line)


def _print_sink(message, stream, end, flush):
    """The default: exactly what these functions did before there was a sink."""
    print(message, end=end, file=stream, flush=flush)


_SINK = _print_sink


def set_quiet(quiet):
    """Silence everything. Returns the previous setting, so a caller can
    restore it rather than assuming it was on."""
    global _QUIET
    previous = _QUIET
    _QUIET = quiet
    return previous


def set_sink(sink):
    """Send every line to sink(message, stream, end, flush); returns the previous sink.
    None restores printing. Call as console.set_sink, never import the value."""
    global _SINK
    previous = _SINK
    _SINK = sink if sink is not None else _print_sink
    return previous


def say(message="", end="\n", flush=False):
    """Normal progress output."""
    if not _QUIET:
        _log(logging.INFO, message, end)
        _SINK(message, sys.stdout, end, flush)


def detail(message=""):
    """Diagnostic output, printed only under --debug."""
    if VERBOSE and not _QUIET:
        _log(logging.DEBUG, message, "\n")
        _SINK(message, sys.stdout, "\n", False)


def warn(message):
    """Something the user needs to see even when stdout is being captured -
    a failed lookup, a permission problem, a refusal to act."""
    if not _QUIET:
        _log(logging.WARNING, message, "\n")
        _SINK(message, sys.stderr, "\n", False)
