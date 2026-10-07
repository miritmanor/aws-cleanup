"""Scrubbing AWS account ids from anything headed for an LLM: every standalone 12-digit
run in every string becomes a deterministic hash placeholder, stable across sessions."""

import hashlib
import re

_ACCOUNT_ID_RE = re.compile(r"\b\d{12}\b")


def _placeholder(account_id: str) -> str:
    digest = hashlib.sha256(account_id.encode()).hexdigest()[:8]
    return f"ACCOUNT-{digest}"


def mask_value(value):
    """Replace each standalone 12-digit run with its placeholder; non-strings pass through."""
    if not isinstance(value, str):
        return value
    return _ACCOUNT_ID_RE.sub(lambda m: _placeholder(m.group()), value)


def mask_row(row: dict) -> dict:
    """A masked copy of `row`, recursing into dicts and lists. Never mutates the input."""
    return _mask_any(row)


def _mask_any(value):
    if isinstance(value, dict):
        return {k: _mask_any(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_any(v) for v in value]
    return mask_value(value)
