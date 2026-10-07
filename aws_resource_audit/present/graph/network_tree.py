"""The nested boxes of a project-scoped architecture diagram: Region > VPC >
subnet, shared by the Mermaid and draw.io renderers so they nest identically."""

from ...rows import member_key
from .groups import grouped_ids, nest_groups
from .labels import rows_by_key


def _box(kind, title):
    return {"kind": kind, "title": title, "nodes": [], "children": []}


def network_tree(graph, all_rows):
    """Region boxes, or None without a network map. Global resources get their own
    box; a node with no VPC sits in its region box, outside every VPC."""
    network = graph.get("network")
    if not network:
        return None
    by_key = rows_by_key(all_rows, member_key)
    groups = graph.get("groups") or []
    # A group spans subnets (an ASG across AZs), so its members sit at VPC level.
    in_group = grouped_ids(groups)
    regions, vpc_boxes, subnet_boxes = {}, {}, {}
    for node in graph.get("nodes") or []:
        region = (by_key.get(node["id"].split("#", 1)[0]) or {}).get("region") or "global"
        region_box = regions.setdefault(region, _box(
            "region", "AWS global services" if region == "global" else f"Region {region}"))
        place = network["placement"].get(node["id"])
        if not place:
            region_box["nodes"].append(node)
            continue
        vpc = place["vpc"]
        if vpc not in vpc_boxes:
            vpc_boxes[vpc] = _box("vpc", f"VPC {network['vpcs'].get(vpc, vpc)}")
            region_box["children"].append(vpc_boxes[vpc])
        subnet = None if node["id"] in in_group else place["subnet"]
        if not subnet:
            vpc_boxes[vpc]["nodes"].append(node)
            continue
        if subnet not in subnet_boxes:
            info = network["subnets"][subnet]
            subnet_boxes[subnet] = _box(
                "public-subnet" if info["public"] else "private-subnet",
                f"{info['name']} · {'public' if info['public'] else 'private'}"
                + (f" · {info['az']}" if info["az"] else ""))
            vpc_boxes[vpc]["children"].append(subnet_boxes[subnet])
        subnet_boxes[subnet]["nodes"].append(node)
    for box in vpc_boxes.values():
        box["children"].sort(key=lambda b: b["title"])
    tree = [regions[r] for r in sorted(regions, key=lambda r: (r == "global", r))]
    for box in tree:
        _nest(box, groups)
    return tree


def _nest(box, groups):
    for child in box["children"]:
        _nest(child, groups)
    box["nodes"], extra = nest_groups(box["nodes"], groups)
    box["children"].extend(extra)
