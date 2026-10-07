"""Test package: puts the repo on sys.path and pins the clock and scan identity, so
golden renders are byte-stable and fixtures stay relative to one stated date."""

import os
import sys
from datetime import datetime, timezone

_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

# Arbitrary but fixed: far enough from the epoch for "730 days ago" to be a real date.
FROZEN_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

# The scan's account, pinned for the same reason; matches the ACCOUNT the connection
# tests build policy ARNs from.
FROZEN_ACCOUNT = "111122223333"

from aws_resource_audit import config, scope  # noqa: E402  (needs the sys.path fixup above)

config.set_now(FROZEN_NOW)
scope.set_scan_scope(FROZEN_ACCOUNT)
