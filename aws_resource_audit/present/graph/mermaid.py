"""The architecture graph as a Mermaid 11 flowchart with AWS icon nodes.
Needs the icons.py pack registered as "aws"."""

from .bands import project_bands
from .groups import nest_groups
from .icons import icon_for
from .labels import mermaid_text, node_label, short_label
from .network_tree import network_tree

# Mermaid ids must be identifier-ish and member_key is not, so ids are positional.
_ID_PREFIX = "n"


def _emit_node(node, ids, lines, indent):
    label = f"{mermaid_text(short_label(node_label(node)))}<br/>{mermaid_text(node.get('service', ''))}"
    if node.get("context"):
        label += "<br/>(other project)"
    lines.append(f'{indent}{ids[node["id"]]}@{{ icon: "{icon_for(node.get("service", ""))}", '
                 f'form: "rounded", label: "{label}", pos: "b", h: 48 }}')


def _emit_edges(edges, ids, lines, indent):
    # Solid is a runtime call or data use; dotted is likely but unproven (grant, name match).
    for edge in edges:
        source, target = ids.get(edge["source"]), ids.get(edge["target"])
        if not source or not target:
            continue
        arrow = "-.->" if edge.get("style") == "dotted" else "-->"
        verb = mermaid_text(edge.get("verb") or "")
        lines.append(f"{indent}{source} {arrow}|{verb}| {target}" if verb
                     else f"{indent}{source} {arrow} {target}")


def _emit_box(box, ids, lines, indent, counter):
    """One Region / VPC / subnet box and everything nested in it."""
    counter[0] += 1
    lines.append(f'{indent}subgraph box{counter[0]}["{mermaid_text(box["title"])}"]')
    for child in box["children"]:
        _emit_box(child, ids, lines, indent + "    ", counter)
    for node in box["nodes"]:
        _emit_node(node, ids, lines, indent + "    ")
    lines.append(f"{indent}end")


def render_mermaid(graph, all_rows, title=None):
    """A `flowchart LR`: one box per project_id (titled with its label); nodes of
    no project, or of another project when scoped, stay outside."""
    ids = {node["id"]: f"{_ID_PREFIX}{i}" for i, node in enumerate(graph["nodes"])}

    lines = ["flowchart LR"]
    if title:
        lines.insert(0, f"---\ntitle: {mermaid_text(title)}\n---")

    tree = network_tree(graph, all_rows)
    if tree:
        counter = [0]
        for box in tree:
            _emit_box(box, ids, lines, "    ", counter)
        _emit_edges(graph["edges"], ids, lines, "    ")
        return "\n".join(lines) + "\n"

    counter = [0]
    groups = graph.get("groups") or []
    for cluster_i, (project, name, nodes) in enumerate(project_bands(graph, all_rows)):
        loose, boxes = nest_groups(nodes, groups)
        indent = "        " if project else "    "
        if project:
            # Positional subgraph ids: a project name may hold characters an id cannot.
            lines.append(f'    subgraph cluster{cluster_i}["{mermaid_text(name)}"]')
        for box in boxes:
            _emit_box(box, ids, lines, indent, counter)
        for node in loose:
            _emit_node(node, ids, lines, indent)
        if project:
            lines.append("    end")

    _emit_edges(graph["edges"], ids, lines, "    ")
    return "\n".join(lines) + "\n"


def write_mermaid(graph, all_rows, path, title=None):
    with open(path, "w") as handle:
        handle.write(render_mermaid(graph, all_rows, title=title))
