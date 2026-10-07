"""What AWS actually returned, per resource, as JSON Lines written and flushed as it
goes, so a crashed scan keeps what it saw. Nothing reads it back; it is account data."""

import logging
import json

from .. import console

logger = logging.getLogger(__name__)

# The open handle for the current scan, or None. Module-level: one scan runs at a time.
_file = None


def start(path):
    """Open `path` for a new scan, truncating the last one's. Failure is reported and
    swallowed: it is only a debugging aid."""
    global _file
    close()
    try:
        _file = open(path, "w")
    except OSError as e:                                    # noqa: BLE001
        _file = None
        console.warn(f"could not open the raw capture file ({e}); "
                     "continuing without it")


def record(service, region, resource_id, raw):
    """Write one resource's unmodified API data immediately. No-op outside a scan;
    default=str since nothing reads it back."""
    if _file is None:
        return
    try:
        json.dump({"service": service, "region": region,
                   "resource_id": resource_id, "raw": raw},
                  _file, default=str)
        _file.write("\n")
        _file.flush()
    except (OSError, TypeError, ValueError) as e:           # noqa: BLE001
        console.warn(f"could not record raw data for {service} {resource_id} "
                     f"({e}); continuing")


def close():
    """Finish the current capture, if there is one. Safe to call twice."""
    global _file
    if _file is None:
        return
    try:
        _file.close()
    except OSError as e:                                    # noqa: BLE001
        logger.warning("could not close the raw capture file (%s); it may be "
                       "truncated", e)
    _file = None
