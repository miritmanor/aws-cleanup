"""Pseudonymizer: real identifiers to tokens on the way to the LLM, and back on the way to
the user. A token is a keyed hash, so the same value always gets the same token."""

import hashlib
import hmac
import threading

from .patterns import (ACCOUNT, CATEGORY_WORDS, NAME, OTHER, PROJECT, RESOURCE,
                       SHAPE_RE, TAG, TOKEN_RE, DIGEST_HEX, known_values_re)

# A display row's identity fields and the kind of token each gets. First kind seen wins.
IDENTITY_FIELDS = (
    ("account", ACCOUNT), ("resource_id", RESOURCE), ("arn", RESOURCE),
    ("resource_key", RESOURCE), ("name", NAME), ("project_id", PROJECT),
    ("project_group", PROJECT),
)
# Values this program chose from a fixed vocabulary: sent as they are.
SCHEMA_FIELDS = frozenset({
    "service", "region", "partition", "tier", "usage_state", "grouping_method",
    "inferred", "flag", "created", "last_used", "last_used_days",
})


class Pseudonymizer:
    """Built from a scan's raw rows; also learns any identifier a regex finds later, so
    reveal() can undo every token it ever handed out."""

    def __init__(self, key: bytes, rows=()):
        self._key = key
        self._lock = threading.Lock()
        self._token_by_real = {}
        self._real_by_token = {}
        for row in rows:
            self._register_row(row)
        self._known_re = known_values_re(self._token_by_real)

    def _register_row(self, row):
        for field, kind in IDENTITY_FIELDS:
            self.token(kind, row.get(field))
        for value in (row.get("tags") or {}).values():
            if str(value).lower() not in CATEGORY_WORDS:
                self.token(TAG, value)
        for ref in row.get("_references") or ():
            self.token(RESOURCE, ref.get("target_id"))

    def token(self, kind, real):
        """The token for `real`, made on first sight. Empty values pass through."""
        if not isinstance(real, str) or not real:
            return real
        with self._lock:
            known = self._token_by_real.get(real)
            if known:
                return known
            digest = hmac.new(self._key, real.encode(), hashlib.sha256).hexdigest()
            width = DIGEST_HEX
            while f"{kind}-{digest[:width]}" in self._real_by_token:
                width += 2
            token = f"{kind}-{digest[:width]}"
            self._token_by_real[real] = token
            self._real_by_token[token] = real
            return token

    def mask_text(self, text):
        """`text` with every identifier replaced: shapes first, then the scan's own values."""
        if not isinstance(text, str) or not text:
            return text
        text = SHAPE_RE.sub(self._shape_token, text)
        if self._known_re is not None:
            text = self._known_re.sub(lambda m: self._token_by_real[m.group()], text)
        return text

    def _shape_token(self, match):
        found = match.group()
        if TOKEN_RE.fullmatch(found):
            return found
        return self._token_by_real.get(found) or self.token(OTHER, found)

    def mask_value(self, value):
        """mask_text applied to every string inside dicts and lists; a copy."""
        if isinstance(value, dict):
            return {k: self.mask_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.mask_value(v) for v in value]
        return self.mask_text(value)

    def mask_row(self, row):
        """A display row as the LLM may see it. Never mutates `row`."""
        identity = dict(IDENTITY_FIELDS)
        out = {}
        for field, value in row.items():
            if field in identity:
                out[field] = self.token(identity[field], value)
            elif field in SCHEMA_FIELDS:
                out[field] = value
            else:
                out[field] = self.mask_value(value)
        return out

    def reveal(self, text):
        """Every token this instance issued, swapped back. Unknown tokens stay as written."""
        if not isinstance(text, str):
            return text
        return TOKEN_RE.sub(lambda m: self._real_by_token.get(m.group(), m.group()), text)
