"""Where each architecture node sits in the network, for the project-scoped
diagram's Region > VPC > subnet boxes. Read off the scan graph's placement links."""

from ..rows import member_key

_SUBNET_TO_VPC = "subnet.vpc.placement"
_TABLE_TO_SUBNET = "routetable.subnet.association"
_TABLE_TO_IGW = "routetable.internetgateway.route"
_TABLE_TO_VPC = "routetable.vpc.placement"


def _is_placement(conn_type):
    return conn_type.endswith(".subnet.placement")


def _links(graph):
    for edge in graph.get("edges") or []:
        if edge.get("state") == "resolved":
            for link in edge.get("links") or []:
                yield edge["source"], edge["target"], link.get("conn_type") or ""


def _public_subnets(graph, rows_by_key, subnet_vpc):
    """Subnets whose route table (their own, else the VPC's main one) reaches an internet gateway."""
    table_subnets, table_vpc, to_igw = {}, {}, set()
    for source, target, conn in _links(graph):
        if conn == _TABLE_TO_SUBNET:
            table_subnets.setdefault(source, set()).add(target)
        elif conn == _TABLE_TO_IGW:
            to_igw.add(source)
        elif conn == _TABLE_TO_VPC:
            table_vpc[source] = target
    associated = {s for subnets in table_subnets.values() for s in subnets}
    public = {s for table in to_igw for s in table_subnets.get(table, ())}
    for table, vpc in table_vpc.items():
        is_main = (rows_by_key.get(table) or {}).get("description") == "main"
        if is_main and table in to_igw:
            public |= {s for s, v in subnet_vpc.items() if v == vpc and s not in associated}
    return public


def network_placement(graph, all_rows, node_ids):
    """{"subnets", "vpcs", "placement"}. A node spanning several subnets (a load balancer,
    a DB subnet group) is placed in their VPC."""
    rows_by_key = {member_key(r): r for r in all_rows}
    subnet_vpc, node_subnets = {}, {}
    for source, target, conn in _links(graph):
        if conn == _SUBNET_TO_VPC:
            subnet_vpc[source] = target
        elif _is_placement(conn) and source in node_ids:
            node_subnets.setdefault(source, set()).add(target)
    public = _public_subnets(graph, rows_by_key, subnet_vpc)
    placement, used_subnets = {}, set()
    for node, subnets in node_subnets.items():
        vpcs = {subnet_vpc.get(s) for s in subnets} - {None}
        if len(vpcs) != 1:
            continue
        subnet = next(iter(subnets)) if len(subnets) == 1 else None
        placement[node] = {"vpc": next(iter(vpcs)), "subnet": subnet}
        if subnet:
            used_subnets.add(subnet)
    subnets = {}
    for sid in sorted(used_subnets):
        row = rows_by_key.get(sid) or {}
        zone = (row.get("description") or "").split(" ")[-1]
        subnets[sid] = {"name": row.get("name") or sid.split(":")[-1], "az": zone,
                        "public": sid in public, "vpc": subnet_vpc.get(sid)}
    vpcs = {v["vpc"]: (rows_by_key.get(v["vpc"]) or {}).get("name") or v["vpc"].split(":")[-1]
            for v in placement.values()}
    return {"subnets": subnets, "vpcs": vpcs, "placement": placement}
