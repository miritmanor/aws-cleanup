"""CHANGELOG.md for a release: the notes a version ships with, and moving its entries out of
"## Unreleased" into "## X.Y.Z". Imported by .github/workflows/release.yml; not a command."""

import re

UNRELEASED = "## Unreleased"


def _heading_re(version):
    """A version's heading: "## 0.2.0" alone or followed by a space or colon."""
    return re.compile(rf"^## {re.escape(version)}([: ]|$)")


def _section(lines, matches):
    """(start, end) of the first section whose heading matches, end exclusive, or None."""
    for start, line in enumerate(lines):
        if matches(line):
            end = start + 1
            while end < len(lines) and not lines[end].startswith("## "):
                end += 1
            return start, end
    return None


def _body(lines, span):
    """A section's lines without its heading or the blank lines around them."""
    body = lines[span[0] + 1:span[1]]
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    return body


def release_notes(text, version):
    """The version's own section if it has one, else what is under Unreleased."""
    lines = text.splitlines()
    span = _section(lines, _heading_re(version).match)
    if span is None:
        span = _section(lines, lambda line: line.strip() == UNRELEASED)
    return "\n".join(_body(lines, span)) if span else ""


def finalize(text, version, entries):
    """`text` with `entries` moved from Unreleased into a new version section below it.
    Unchanged if the version already has a section, so a re-run does nothing."""
    lines = text.splitlines()
    if _section(lines, _heading_re(version).match) or not entries.strip():
        return text
    span = _section(lines, lambda line: line.strip() == UNRELEASED)
    if span is None:
        raise ValueError(f"CHANGELOG.md has no '{UNRELEASED}' section")
    shipped = {line for line in entries.splitlines() if line.strip()}
    # Matched line by line: an entry added to main after the tag stays under Unreleased.
    remaining = [line for line in _body(lines, span) if line not in shipped]
    block = [UNRELEASED, ""] + remaining + ([""] if remaining else [])
    block += [f"## {version}", "", *entries.strip("\n").splitlines(), ""]
    out = lines[:span[0]] + block + lines[span[1]:]
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")
