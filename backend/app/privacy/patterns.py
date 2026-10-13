"""The regular expressions: what a token looks like, and the identifier shapes caught even
when the scan never recorded them (an ARN in a note, an IP in an uploaded runbook)."""

import re

# Token kinds. The kind tells the model what the value was without saying which one.
ACCOUNT, RESOURCE, NAME, PROJECT, TAG, OTHER = "ACCT", "RES", "NAME", "PROJ", "TAG", "ID"
KINDS = (ACCOUNT, RESOURCE, NAME, PROJECT, TAG, OTHER)
DIGEST_HEX = 10

TOKEN_RE = re.compile(r"(?<![\w-])(?:%s)-[0-9a-f]{%d,}(?![\w-])" % ("|".join(KINDS), DIGEST_HEX))

# Ordered: at one position the first alternative wins, so an ARN beats the account id in it.
_SHAPES = (
    r"arn:aws[\w-]*:[^\s\"'`,;)\]}>]+",
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+",
    r"\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.){2,}[a-z]{2,}\b",
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    r"\b[a-z]{1,9}(?:-[a-z]{1,9})?-[0-9a-f]{8,17}\b",
    r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b",
    r"(?<!\d)\d{12}(?!\d)",
    r"\b[A-Za-z0-9]{20,}\b",
)
SHAPE_RE = re.compile("|".join(f"(?:{s})" for s in _SHAPES), re.IGNORECASE)

# Tag values that say what kind of thing a resource is, not which one: kept readable.
CATEGORY_WORDS = frozenset({
    "prod", "production", "dev", "development", "staging", "stage", "test", "testing",
    "qa", "uat", "sandbox", "demo", "true", "false", "yes", "no", "enabled", "disabled",
})

# Below this length, or if one of these words, a known value is not searched for in free text.
MIN_TEXT_MATCH = 3
GENERIC_WORDS = CATEGORY_WORDS | {"default", "main", "master", "api", "app", "web", "none"}


def known_values_re(values):
    """One regex matching any of `values` as a whole word, longest first, or None."""
    words = sorted({v for v in values if len(v) >= MIN_TEXT_MATCH
                    and v.lower() not in GENERIC_WORDS}, key=len, reverse=True)
    if not words:
        return None
    alternation = "|".join(map(re.escape, words))
    return re.compile(rf"(?<![\w-])(?:{alternation})(?![\w-])")
