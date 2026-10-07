"""Which project box each architecture node is drawn in, shared by the Mermaid
and draw.io renderers so the two cannot box a resource differently."""

from ...rows import member_key
from .labels import rows_by_key


def project_bands(graph, all_rows):
    """[(project_id, title, nodes)] sorted by title, then ("", None, rest). Context
    nodes from another project go with the rest, outside every box."""
    by_key = rows_by_key(all_rows, member_key)
    grouped, names, rest = {}, {}, []
    for node in graph.get("nodes") or []:
        row = by_key.get(node["id"].split("#", 1)[0]) or {}
        project = "" if node.get("context") else (row.get("project_id") or "")
        if project:
            names.setdefault(project, row.get("project_group") or project)
            grouped.setdefault(project, []).append(node)
        else:
            rest.append(node)
    bands = [(p, names[p], grouped[p]) for p in sorted(grouped, key=lambda p: names[p].lower())]
    return bands + ([("", None, rest)] if rest else [])
