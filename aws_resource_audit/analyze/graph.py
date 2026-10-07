"""The dependency graph: build_graph_data (pops row["_graph"], so runs once) and
contract_graph (keeps app resources, bridges one hop). One module: they share merge rules."""

from ..config import (
    NON_BRIDGING_SERVICES,
    STRUCTURAL_SERVICES,
)
from ..connection_types.vocabulary import CONF_AUTHORITATIVE, CONF_CONFIG_REFERENCE, CONF_HEURISTIC
from ..rows import member_key


# When several detections of one pair collapse into one edge, the strongest wins.
_CONFIDENCE_RANK = {CONF_AUTHORITATIVE: 3, CONF_CONFIG_REFERENCE: 2, CONF_HEURISTIC: 1}


def _merge_edge(edges, source, target, *, category, state, confidence, link,
                bridged=False, via=None):
    """Record source->target, merging into an existing edge for the same pair. Shared
    with contract_graph so bridged edges merge by the same rules. True if merged."""
    if source == target:
        return False
    existing = edges.get((source, target))
    if existing is None:
        edge = {
            "source": source, "target": target, "category": category,
            "state": state, "grouping": bool(link.get("grouping")),
            "confidence": confidence, "links": [link],
        }
        if bridged:
            edge["bridged"] = True
            edge["via"] = list(via or [])
        edges[(source, target)] = edge
        return False
    # The same pair found twice: collapse, keeping every reason for the inspector.
    existing["links"].append(link)
    existing["grouping"] = existing["grouping"] or bool(link.get("grouping"))
    if _CONFIDENCE_RANK.get(confidence, 0) > _CONFIDENCE_RANK.get(existing["confidence"], 0):
        existing["confidence"] = confidence
    # A real link explains a pair better than "these share a tag", so it
    # wins the category even when the attribute union was recorded first.
    if category == "link" and existing["category"] == "attribute":
        existing["category"] = category
        existing["state"] = state
    # Likewise a direct link outranks one inferred through hidden plumbing: an
    # edge stops being "bridged" as soon as any un-bridged detection backs it.
    if bridged:
        if existing.get("bridged"):
            seen = {(v["service"], v["label"]) for v in existing["via"]}
            existing["via"].extend(v for v in (via or [])
                                   if (v["service"], v["label"]) not in seen)
    else:
        existing.pop("bridged", None)
        existing.pop("via", None)
    return True


def build_graph_data(all_rows, grouping_links=None):
    """Build the node/edge payload from resolved links and grouping unions. Runs AFTER
    grouping, and POPS row["_graph"] as it goes. Nodes carry keys, not row attributes."""
    nodes = {}      # node id -> node dict, insertion-ordered
    edges = {}      # (source, target) -> edge dict
    key_by_idx = {}
    merged = 0

    def node_id_for(idx):
        row = all_rows[idx]
        key = member_key(row)
        # A duplicate member_key would fold two resources into one node; suffix it.
        if key in nodes and nodes[key].get("_idx") != idx:
            n = 2
            while f"{key}#{n}" in nodes:
                n += 1
            key = f"{key}#{n}"
        nodes.setdefault(key, {
            "id": key, "kind": "resource", "service": row.get("service", ""),
            "label": row.get("name") or row.get("resource_id", ""),
            "hub": row.get("service") in NON_BRIDGING_SERVICES, "_idx": idx,
        })
        return key

    for idx in range(len(all_rows)):
        key_by_idx[idx] = node_id_for(idx)

    def add(source, target, *, category, state, confidence, link):
        nonlocal merged
        if _merge_edge(edges, source, target, category=category, state=state,
                       confidence=confidence, link=link):
            merged += 1

    for idx, row in enumerate(all_rows):
        for rec in row.get("_references", []):
            if rec["kind"] not in ("resolved", "dangling"):
                continue   # incoming is the same link seen from the other end

            if rec["kind"] == "dangling":
                # "?:" cannot collide with a member_key; scoped so same-named targets
                # in different regions stay separate ghost nodes.
                scope = rec.get("target_region") or rec.get("target_account") or "*"
                target = (f"?:{rec.get('target_service') or '*'}"
                          f":{scope}:{rec['target_id']}")
                if target not in nodes:
                    nodes[target] = {
                        "id": target, "kind": "stub",
                        "service": rec.get("target_service") or "",
                        "label": rec["target_id"], "hub": False, "_idx": None,
                    }
            else:
                target = key_by_idx[rec["target_idx"]]
            add(key_by_idx[idx], target,
                # The edge state is the reference kind: resolved or dangling.
                category="link", state=rec["kind"], confidence=rec["confidence"],
                link={"rel": rec["rel"], "conn_type": rec["conn_type"],
                      "evidence": rec["evidence"], "confidence": rec["confidence"],
                      "grouping": rec["grouping"]})

    for gl in (grouping_links or []):
        add(key_by_idx[gl["source_idx"]], key_by_idx[gl["target_idx"]],
            category="attribute", state="attribute", confidence=CONF_HEURISTIC,
            link={"rel": gl.get("reason") or "", "conn_type": gl["conn_type"],
                  "evidence": gl.get("reason") or "", "confidence": CONF_HEURISTIC,
                  "grouping": True})

    node_list = []
    for nd in nodes.values():
        nd = dict(nd)
        nd.pop("_idx", None)
        node_list.append(nd)
    edge_list = list(edges.values())

    return {
        "nodes": node_list,
        "edges": edge_list,
        "stats": _graph_stats(node_list, edge_list, merged=merged),
    }


def _graph_stats(node_list, edge_list, **extra):
    """The graph's counts line, shared so full and contracted payloads use one vocabulary."""
    stats = {
        "nodes": len(node_list),
        "stubs": sum(1 for n in node_list if n.get("kind") == "stub"),
        "edges": len(edge_list),
        "grouping_edges": sum(1 for e in edge_list
                              if e["category"] == "link" and e["grouping"]),
        "report_only_edges": sum(1 for e in edge_list
                                 if e["category"] == "link" and not e["grouping"]),
        "dangling_edges": sum(1 for e in edge_list if e["state"] == "dangling"),
        "attribute_edges": sum(1 for e in edge_list if e["category"] == "attribute"),
        "bridged_edges": sum(1 for e in edge_list if e.get("bridged")),
    }
    stats.update(extra)
    return stats


def contract_graph(graph, structural=STRUCTURAL_SERVICES):
    """Keep only STRUCTURAL_SERVICES nodes, redrawing paths through a removed resource as
    "via" edges: exactly ONE hop, and never through NON_BRIDGING_SERVICES."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    by_id = {n["id"]: n for n in nodes}

    kept, hidden = set(), set()
    for n in nodes:
        if n.get("service") in structural:
            kept.add(n["id"])
        elif n.get("service") not in NON_BRIDGING_SERVICES and n.get("kind") != "stub":
            # A stub is out of scan, so it can only be a dead end.
            hidden.add(n["id"])

    # "Attribute" (shared-union) edges count both ways: a function's link to its role
    # exists only as such a union, so treating it as directed would bridge nothing.
    def stronger(a, b):
        if a is None:
            return b
        return b if _CONFIDENCE_RANK.get(b["confidence"], 0) > \
            _CONFIDENCE_RANK.get(a["confidence"], 0) else a

    ins = {h: {} for h in hidden}
    outs = {h: {} for h in hidden}
    for e in edges:
        s, t = e["source"], e["target"]
        undirected = e["category"] == "attribute"
        if s in hidden and t in kept:
            outs[s][t] = stronger(outs[s].get(t), e)
            if undirected:
                ins[s][t] = stronger(ins[s].get(t), e)
        elif t in hidden and s in kept:
            ins[t][s] = stronger(ins[t].get(s), e)
            if undirected:
                outs[t][s] = stronger(outs[t].get(s), e)

    def hop_conn_types(e):
        return "/".join(dict.fromkeys(l.get("conn_type", "") for l in e["links"]
                                      if l.get("conn_type")))

    def hop_rel(e):
        return next((l.get("rel") for l in e["links"] if l.get("rel")), e["category"])

    out_edges = {}
    merged = graph.get("stats", {}).get("merged", 0)
    for e in edges:
        if e["source"] in kept and e["target"] in kept:
            out_edges[(e["source"], e["target"])] = dict(e, links=list(e["links"]))

    for h in sorted(hidden):
        via = [{"service": by_id[h].get("service", ""), "label": by_id[h].get("label", "")}]
        via_label = f"{via[0]['service']} {via[0]['label']}".strip()
        for u, first in ins[h].items():
            for v, second in outs[h].items():
                if u == v:
                    continue
                # A bridge is as good as its weaker half, and merges only if both may.
                confidence = min(first["confidence"], second["confidence"],
                                 key=lambda c: _CONFIDENCE_RANK.get(c, 0))
                if _merge_edge(
                        out_edges, u, v,
                        category=("link" if first["category"] == "link"
                                  and second["category"] == "link" else "attribute"),
                        state="resolved",
                        confidence=confidence,
                        bridged=True, via=via,
                        link={"rel": f"via {via_label}",
                              "conn_type": f"bridge:{hop_conn_types(first)}"
                                           f"+{hop_conn_types(second)}",
                              "evidence": f"{hop_rel(first)} -> {via_label}"
                                          f" -> {hop_rel(second)}",
                              "confidence": confidence,
                              "grouping": first["grouping"] and second["grouping"]}):
                    merged += 1

    node_list = [dict(n) for n in nodes if n["id"] in kept]
    edge_list = list(out_edges.values())
    return {
        "nodes": node_list,
        "edges": edge_list,
        "stats": _graph_stats(node_list, edge_list, merged=merged,
                              scanned_nodes=len(nodes),
                              hidden_nodes=len(nodes) - len(node_list)),
    }


def extract_project(graph, all_rows, project_id):
    """One project's subgraph (owned nodes) plus one hop of `context` nodes, which are
    drawn outside the boundary and never counted. Matches on project_id, never the label."""
    owned_keys = {member_key(row) for row in all_rows
                  if (row.get("project_id") or "") == project_id}
    if not owned_keys:
        return {"nodes": [], "edges": [], "stats": _graph_stats([], [])}

    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    # Node ids may be suffixed ("key#2"), so test the unsuffixed form too.
    def owns(node_id):
        return node_id in owned_keys or node_id.split("#", 1)[0] in owned_keys

    owned_ids = {n["id"] for n in nodes if owns(n["id"])}

    context_ids = set()
    kept_edges = []
    for edge in edges:
        source, target = edge["source"], edge["target"]
        in_source, in_target = source in owned_ids, target in owned_ids
        if in_source and in_target:
            kept_edges.append(edge)
        elif in_source or in_target:
            # One end is ours: keep the edge and the far node as context.
            context_ids.add(target if in_source else source)
            kept_edges.append(edge)

    keep = owned_ids | context_ids
    node_list = [dict(n, context=n["id"] not in owned_ids)
                 for n in nodes if n["id"] in keep]
    edge_list = [dict(e, links=list(e.get("links") or [])) for e in kept_edges]
    return {
        "nodes": node_list,
        "edges": edge_list,
        "stats": _graph_stats(node_list, edge_list,
                              owned_nodes=len(owned_ids),
                              context_nodes=len(context_ids)),
    }
