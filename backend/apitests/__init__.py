"""Backend tests: no AWS, no network, a fresh AUDIT_DATA_DIR per test, fixtures from the
core's synthetic tests/corpus.py. Not named `tests`, which would shadow the core suite."""

import os
import sys

# The repo root, so `tests.corpus` (the core suite's fixtures) is importable.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

# backend/, so `app` is importable without installing it.
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# Importing app.main configures logging; setdefault so AUDIT_LOG_LEVEL=DEBUG still works.
os.environ.setdefault("AUDIT_LOG_LEVEL", "CRITICAL")
