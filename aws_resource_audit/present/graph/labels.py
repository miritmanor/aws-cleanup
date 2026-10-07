"""What a node is called in any renderer. Long names are cut in the MIDDLE, since the
tail is what tells neighbouring resources apart."""

# Characters, not pixels. Cytoscape wraps the result and Mermaid does not, so
# this is the budget at which a name still reads as a name in both.
LABEL_BUDGET = 42

# Mermaid cannot escape its delimiters, so they are replaced; labels are emitted quoted.
_MERMAID_UNSAFE = {
    '"': "'",
    "[": "(", "]": ")",
    "{": "(", "}": ")",
    "|": "/",
    "<": "(", ">": ")",
    "\n": " ",
}


def short_label(text, budget=LABEL_BUDGET):
    """Trim to `budget` characters with a middle ellipsis, keeping slightly more head."""
    text = str(text or "")
    if len(text) <= budget:
        return text
    head = -(-(budget - 1) * 55 // 100)      # ceil((budget - 1) * 0.55)
    tail = budget - 1 - head
    return text[:head] + "…" + (text[len(text) - tail:] if tail else "")


def node_label(node):
    """The full, untruncated name of a node."""
    return node.get("label") or node.get("id", "")


def mermaid_text(text):
    """Make a label safe to sit inside a Mermaid node."""
    return "".join(_MERMAID_UNSAFE.get(ch, ch) for ch in str(text or ""))


def rows_by_key(all_rows, member_key):
    """node id (member_key) -> row: nodes carry no row attributes."""
    return {member_key(row): row for row in all_rows}
