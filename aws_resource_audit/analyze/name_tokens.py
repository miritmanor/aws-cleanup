"""Splitting resource names into words worth grouping on - the low-confidence end of
grouping, fenced by stopwords, standalone-word and breadth checks."""

import re

from ..config import (
    NAME_TOKEN_MIN_LEN,
    NAME_TOKEN_MIN_SUBSTRING,
    NAME_TOKEN_STOPWORDS,
)


def plain_name_tokens(text):
    """Split on punctuation only: runs someone typed as one unit, trusted for substrings."""
    return [t for t in re.split(r"[^A-Za-z0-9]+", (text or "").lower()) if t]


def split_name_tokens(text):
    """Lowercase word-split both by punctuation and camelCase, since each alone loses
    words ("ShopUploadRole" vs "WebShop"). Extra tokens only add candidate matches."""
    camel = re.split(r"[^A-Za-z0-9]+",
                     re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "").lower())
    return [t for t in dict.fromkeys(plain_name_tokens(text) + camel) if t]


_MIN_STOPWORD_LEN = min(len(w) for w in NAME_TOKEN_STOPWORDS)


def is_all_stopwords(token):
    """True if the token is only generic words run together, e.g.
    "awslambdabasicexecutionrole" from an AWS-managed policy name."""
    n = len(token)
    reachable = [False] * (n + 1)
    reachable[0] = True
    for i in range(n):
        if not reachable[i]:
            continue
        for j in range(i + _MIN_STOPWORD_LEN, n + 1):
            if token[i:j] in NAME_TOKEN_STOPWORDS:
                reachable[j] = True
    return reachable[n]


def distinctive_name_tokens(text):
    """Tokens worth grouping on: long enough, not a pure number, not house vocabulary."""
    return {t for t in split_name_tokens(text)
            if len(t) >= NAME_TOKEN_MIN_LEN
            and not t.isdigit()
            and t not in NAME_TOKEN_STOPWORDS
            and not is_all_stopwords(t)}


def owner_name_tokens(all_rows):
    """Words naming the account's people (IAM user names, whole tokens only), so an
    owner's name stamped on unrelated things does not look like a project."""
    tokens = set()
    for row in all_rows:
        if row.get("service") == "IAMUser":
            tokens |= distinctive_name_tokens(row.get("name") or "")
    return tokens


def build_name_token_index(names):
    """Distinctive token -> row indexes whose name contains it. A second pass finds known
    words inside run-together names, fenced by length, standalone use and position."""
    plain = [plain_name_tokens(n) for n in names]
    standalone = {t for toks in plain for t in toks}

    index = {}
    for i, name in enumerate(names):
        for t in distinctive_name_tokens(name):
            index.setdefault(t, set()).add(i)

    for token, holders in list(index.items()):
        if len(token) < NAME_TOKEN_MIN_SUBSTRING or token not in standalone:
            continue
        for i, tokens in enumerate(plain):
            if i in holders:
                continue
            if any(tok != token and (tok.startswith(token) or tok.endswith(token))
                   for tok in tokens):
                holders.add(i)
    return index
