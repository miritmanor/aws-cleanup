"""Positions for the .drawio architecture diagram, computed here because draw.io
does not lay a file out on open. Pure arithmetic: same graph, same coordinates."""

# Left-to-right reading order of an AWS reference diagram. Unknown types sit with compute.
EDGE, ENTRY, COMPUTE, MESSAGING, DATA = range(5)
COLUMN_OF = {
    "CloudFrontDistribution": EDGE, "Route53HostedZone": EDGE, "WAFWebACL": EDGE,
    "Route53ResolverEndpoint": EDGE,
    "NetworkFirewall": EDGE, "TransitGateway": EDGE, "VPNConnection": EDGE,
    "APIGatewayRestApi": ENTRY, "APIGatewayV2Api": ENTRY, "LoadBalancer": ENTRY, "AppSyncApi": ENTRY,
    "AmplifyApp": ENTRY, "CognitoUserPool": ENTRY,
    "EventBridgeRule": ENTRY, "EventBridgeSchedule": ENTRY,
    "EC2Instance": COMPUTE, "LambdaFunction": COMPUTE, "ElasticBeanstalkEnvironment": COMPUTE,
    "StepFunctionsStateMachine": COMPUTE, "ECSService": COMPUTE, "SageMakerEndpoint": COMPUTE,
    "EKSCluster": COMPUTE,
    "SQSQueue": MESSAGING, "SNSTopic": MESSAGING, "EventBridgeBus": MESSAGING,
    "KinesisStream": MESSAGING, "FirehoseStream": MESSAGING, "MSKCluster": MESSAGING,
    "S3Bucket": DATA, "EFSFileSystem": DATA, "RDSInstance": DATA,
    "ElastiCacheCluster": DATA, "OpenSearchDomain": DATA,
    "OpenSearchServerlessCollection": DATA, "RedshiftCluster": DATA,
    "RedshiftServerlessWorkgroup": DATA,
    "DocumentDBCluster": DATA, "DocumentDBInstance": DATA, "NeptuneCluster": DATA,
    "NeptuneInstance": DATA, "RDSCluster": DATA, "DynamoDBTable": DATA,
}

ICON = 48          # icon side, px
COL_W = 200        # column pitch; the label under an icon needs the width
ROW_H = 105        # row pitch; icon plus two label lines
BAND_GAP = 30
CLOUD_PAD, CLOUD_TOP = 30, 50
MAX_ROWS = 6       # a longer column wraps into the next one, so a band stays wide rather than tall


def _neighbours(edges):
    out = {}
    for e in edges:
        out.setdefault(e["source"], set()).add(e["target"])
        out.setdefault(e["target"], set()).add(e["source"])
    return out


def _order_columns(nodes, neighbours):
    """{column: [node, ...]} with each column sorted by the mean row of its
    already-placed neighbours (one barycentre sweep), ties by label then id."""
    columns = {}
    for node in nodes:
        columns.setdefault(COLUMN_OF.get(node.get("service"), COMPUTE), []).append(node)
    row_of = {}
    for col in sorted(columns):
        def key(node):
            rows = [row_of[n] for n in neighbours.get(node["id"], ()) if n in row_of]
            centre = sum(rows) / len(rows) if rows else float("inf")
            return (centre, (node.get("label") or "").lower(), node["id"])
        columns[col].sort(key=key)
        for i, node in enumerate(columns[col]):
            row_of[node["id"]] = i % MAX_ROWS
    return [columns[c][i:i + MAX_ROWS] for c in sorted(columns)
            for i in range(0, len(columns[c]), MAX_ROWS)]


BOX_PAD, BOX_TOP, BOX_GAP, BOX_MIN_W = 25, 45, 25, 230
LABEL_ROOM = 40    # the two label lines under the lowest icon


def _block(nodes, neighbours):
    """(width, height, [(node, x, y)]) of a box's own nodes, columns as in a band."""
    if not nodes:
        return 0, 0, []
    columns = _order_columns(nodes, neighbours)
    placed = [(n, c * COL_W, r * ROW_H) for c, col in enumerate(columns) for r, n in enumerate(col)]
    tallest = max(len(col) for col in columns)
    return (len(columns) - 1) * COL_W + ICON, (tallest - 1) * ROW_H + ICON + LABEL_ROOM, placed


def layout_box(box, neighbours):
    """A Region / VPC / subnet box, sized around its nodes and child boxes.
    Subnets sit side by side, like AZ columns; other children stack downwards."""
    own_w, own_h, own = _block(box["nodes"], neighbours)
    children = [layout_box(child, neighbours) for child in box["children"]]
    side_by_side = bool(children) and all(c["kind"].endswith("subnet") for c in children)
    y = BOX_TOP + (own_h + BOX_GAP if own else 0)
    x, placed_children, inner_w, inner_h = BOX_PAD, [], own_w, y
    for child in children:
        placed_children.append((child, x, y))
        if side_by_side:
            x += child["w"] + BOX_GAP
            inner_w = max(inner_w, x - BOX_GAP - BOX_PAD)
            inner_h = max(inner_h, y + child["h"])
        else:
            y += child["h"] + BOX_GAP
            inner_w = max(inner_w, child["w"])
            inner_h = y - BOX_GAP
    return {"kind": box["kind"], "title": box["title"],
            "w": max(BOX_MIN_W, inner_w + 2 * BOX_PAD), "h": inner_h + BOX_PAD,
            "nodes": [(n, BOX_PAD + nx, BOX_TOP + ny) for n, nx, ny in own],
            "children": placed_children}


def layout_tree(tree, graph):
    """{"boxes": [(box, x, y)], "width", "height"}: top-level boxes stacked in the cloud."""
    neighbours = _neighbours(graph.get("edges") or [])
    boxes, y, width = [], CLOUD_TOP, 0
    for region in tree:
        box = layout_box(region, neighbours)
        boxes.append((box, CLOUD_PAD, y))
        y += box["h"] + BAND_GAP
        width = max(width, box["w"])
    return {"boxes": boxes, "width": width + 2 * CLOUD_PAD, "height": y - BAND_GAP + CLOUD_PAD}
