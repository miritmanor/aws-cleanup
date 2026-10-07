"""Nesting a container's nodes into group boxes (Auto Scaling group, EKS cluster),
shared by the project and network layouts of both diagram formats."""


def _has_content(box):
    box["children"] = [c for c in box["children"] if _has_content(c)]
    return bool(box["nodes"] or box["children"])


def nest_groups(nodes, groups):
    """(loose nodes, group boxes) for the nodes of one container; a group whose
    members are all elsewhere yields no box."""
    by_id = {n["id"]: n for n in nodes}
    boxes, parents = {}, {}
    for group in groups:
        boxes[group["id"]] = {"kind": group["kind"], "title": group["title"], "children": [],
                              "nodes": [by_id[m] for m in group["members"] if m in by_id]}
        parents[group["id"]] = group.get("parent")
    top = []
    for gid, box in boxes.items():
        (boxes[parents[gid]]["children"] if parents[gid] in boxes else top).append(box)
    placed = {n["id"] for box in boxes.values() for n in box["nodes"]}
    return [n for n in nodes if n["id"] not in placed], [b for b in top if _has_content(b)]


def grouped_ids(groups):
    return {m for g in groups for m in g["members"]}
