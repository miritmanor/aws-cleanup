"""The architecture graph as a native draw.io file, with draw.io's own AWS4 shapes,
so every box and line stays editable. Positions come from drawio_layout.py."""

import xml.etree.ElementTree as ET

from .bands import project_bands
from .drawio_layout import ICON, layout_tree
from .groups import nest_groups
from .labels import node_label, short_label
from .network_tree import network_tree

# draw.io's resIcon name and AWS category colour, as its "AWS" sidebar draws them.
COMPUTE, STORAGE, DATABASE = "#ED7100", "#7AA116", "#C925D1"
INTEGRATION, FRONTEND, NETWORK, SECURITY = "#E7157B", "#DD344C", "#8C4FFF", "#DD344C"
DRAWIO_SHAPES = {
    "EC2Instance": ("ec2", COMPUTE),
    "LambdaFunction": ("lambda", COMPUTE),
    "ElasticBeanstalkEnvironment": ("elastic_beanstalk", COMPUTE),
    "S3Bucket": ("s3", STORAGE),
    "RDSInstance": ("rds", DATABASE),
    "RDSCluster": ("aurora", DATABASE),
    "DocumentDBCluster": ("documentdb_with_mongodb_compatibility", DATABASE),
    "DocumentDBInstance": ("documentdb_with_mongodb_compatibility", DATABASE),
    "NeptuneCluster": ("neptune", DATABASE),
    "NeptuneInstance": ("neptune", DATABASE),
    "EFSFileSystem": ("elastic_file_system", STORAGE),
    "ElastiCacheCluster": ("elasticache", DATABASE),
    "OpenSearchDomain": ("elasticsearch_service", NETWORK),
    "OpenSearchServerlessCollection": ("elasticsearch_service", NETWORK),
    "CloudFrontDistribution": ("cloudfront", NETWORK),
    "Route53HostedZone": ("route_53", NETWORK),
    "Route53ResolverEndpoint": ("route_53_resolver", NETWORK),
    "WAFWebACL": ("waf", SECURITY),
    "NetworkFirewall": ("network_firewall", SECURITY),
    "TransitGateway": ("transit_gateway", NETWORK),
    "VPNConnection": ("site_to_site_vpn", NETWORK),
    "StepFunctionsStateMachine": ("step_functions", INTEGRATION),
    "ECSService": ("ecs", COMPUTE),
    "SageMakerEndpoint": ("sagemaker", "#01A88D"),
    "EKSCluster": ("eks", COMPUTE),
    "RedshiftCluster": ("redshift", DATABASE),
    "RedshiftServerlessWorkgroup": ("redshift", DATABASE),
    "DynamoDBTable": ("dynamodb", DATABASE),
    "AmplifyApp": ("amplify", FRONTEND),
    "APIGatewayRestApi": ("api_gateway", INTEGRATION),
    "APIGatewayV2Api": ("api_gateway", INTEGRATION),
    "AppSyncApi": ("appsync", INTEGRATION),
    "LoadBalancer": ("elastic_load_balancing", NETWORK),
    "CognitoUserPool": ("cognito", SECURITY),
    "SQSQueue": ("sqs", INTEGRATION),
    "KinesisStream": ("kinesis_data_streams", NETWORK),
    "FirehoseStream": ("kinesis_data_firehose", NETWORK),
    "MSKCluster": ("managed_streaming_for_kafka", NETWORK),
    "SNSTopic": ("sns", INTEGRATION),
    "EventBridgeRule": ("eventbridge", INTEGRATION),
    "EventBridgeSchedule": ("eventbridge", INTEGRATION),
    "EventBridgeBus": ("eventbridge", INTEGRATION),
}
_FALLBACK = ("general", "#232F3E")

_ICON_STYLE = ("sketch=0;outlineConnect=0;fontColor=#232F3E;fillColor={fill};strokeColor=#ffffff;"
               "dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;"
               "fontSize=11;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{icon};")
_CLOUD_STYLE = ("points=[];outlineConnect=0;gradientColor=none;html=1;whiteSpace=wrap;fontSize=12;"
                "container=1;pointerEvents=0;collapsible=0;recursiveResize=0;shape=mxgraph.aws4.group;"
                "grIcon=mxgraph.aws4.group_aws_cloud_alt;strokeColor=#232F3E;fillColor=none;"
                "verticalAlign=top;align=left;spacingLeft=30;fontColor=#232F3E;dashed=0;")
_BAND_STYLE = ("rounded=1;arcSize=4;whiteSpace=wrap;html=1;container=1;collapsible=0;"
               "fillColor=none;strokeColor={stroke};dashed={dashed};verticalAlign=top;align=left;"
               "spacingLeft=10;fontStyle=1;fontColor=#232F3E;")
# draw.io's own AWS group shapes for the network boxes, in AWS's diagram colours.
_GROUP = ("points=[];outlineConnect=0;gradientColor=none;html=1;whiteSpace=wrap;fontSize=12;"
          "container=1;pointerEvents=0;collapsible=0;recursiveResize=0;shape=mxgraph.aws4.group;"
          "verticalAlign=top;align=left;spacingLeft=30;dashed={dashed};"
          "grIcon=mxgraph.aws4.{icon};strokeColor={stroke};fillColor={fill};fontColor={stroke};")
_BOX_STYLES = {
    "project": _BAND_STYLE.format(stroke="#147EBA", dashed=0),
    "unowned": _BAND_STYLE.format(stroke="#879196", dashed=1),
    "asg": _GROUP.format(icon="group_auto_scaling_group", stroke="#ED7100", fill="none", dashed=1),
    "eks": _BAND_STYLE.format(stroke="#ED7100", dashed=0),
    "ecs": _BAND_STYLE.format(stroke="#ED7100", dashed=0),
    "region": _GROUP.format(icon="group_region", stroke="#00A4A6", fill="none", dashed=1),
    "vpc": _GROUP.format(icon="group_vpc2", stroke="#8C4FFF", fill="none", dashed=0),
    "public-subnet": _GROUP.format(icon="group_security_group", stroke="#7AA116", fill="#F2F6E8", dashed=0),
    "private-subnet": _GROUP.format(icon="group_security_group", stroke="#00A4A6", fill="#E6F6F7", dashed=0),
}

_EDGE_STYLE = ("edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;"
               "strokeColor=#545B64;fontSize=10;fontColor=#545B64;labelBackgroundColor=#ffffff;")

# Characters, not pixels: what fits under an icon within one column.
LABEL_BUDGET = 30


def _cell(root, cell_id, parent, *, value="", style="", vertex=False, edge=False,
          geometry=None, source=None, target=None):
    attrs = {"id": cell_id, "parent": parent, "value": value, "style": style}
    if vertex:
        attrs["vertex"] = "1"
    if edge:
        attrs.update(edge="1", source=source, target=target)
    cell = ET.SubElement(root, "mxCell", attrs)
    geo = ET.SubElement(cell, "mxGeometry", {"as": "geometry"})
    if geometry:
        for name, number in zip(("x", "y", "width", "height"), geometry):
            geo.set(name, str(round(number)))
    else:
        geo.set("relative", "1")
    return cell


def _project_boxes(graph, all_rows):
    """The account view's project bands as boxes, each nesting its group boxes."""
    groups = graph.get("groups") or []
    boxes = []
    for project, title, nodes in project_bands(graph, all_rows):
        loose, children = nest_groups(nodes, groups)
        boxes.append({
            "kind": "project" if project else "unowned", "nodes": loose, "children": children,
            "title": title or ("Outside this project" if any(n.get("context") for n in nodes)
                               else "Not in a project")})
    return boxes


def _node_cell(root, ids, node, parent, x, y):
    ids[node["id"]] = node_id = f"n{len(ids)}"
    icon, fill = DRAWIO_SHAPES.get(node.get("service"), _FALLBACK)
    label = f"{short_label(node_label(node), LABEL_BUDGET)}<br>{node.get('service', '')}"
    if node.get("context"):
        label += "<br>(other project)"
    _cell(root, node_id, parent, value=label, vertex=True,
          style=_ICON_STYLE.format(fill=fill, icon=icon), geometry=(x, y, ICON, ICON))


def _emit_box(root, ids, box, parent, x, y, counter):
    counter[0] += 1
    box_id = f"box{counter[0]}"
    _cell(root, box_id, parent, value=box["title"], vertex=True,
          style=_BOX_STYLES[box["kind"]], geometry=(x, y, box["w"], box["h"]))
    for child, cx, cy in box["children"]:
        _emit_box(root, ids, child, box_id, cx, cy, counter)
    for node, nx, ny in box["nodes"]:
        _node_cell(root, ids, node, box_id, nx, ny)


def render_drawio(graph, all_rows, title="AWS architecture"):
    """An uncompressed .drawio (mxfile) document as a string."""
    placed = layout_tree(network_tree(graph, all_rows) or _project_boxes(graph, all_rows), graph)
    mxfile = ET.Element("mxfile", {"host": "aws-resource-audit"})
    diagram = ET.SubElement(mxfile, "diagram", {"id": "architecture", "name": title})
    model = ET.SubElement(diagram, "mxGraphModel", {
        "grid": "1", "gridSize": "10", "guides": "1", "arrows": "1", "connect": "1",
        "page": "0", "math": "0", "shadow": "0"})
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})

    _cell(root, "cloud", "1", value="AWS Cloud", style=_CLOUD_STYLE, vertex=True,
          geometry=(0, 0, placed["width"], placed["height"]))
    ids = {}
    counter = [0]
    for box, x, y in placed["boxes"]:
        _emit_box(root, ids, box, "cloud", x, y, counter)

    for e, edge in enumerate(graph.get("edges") or []):
        source, target = ids.get(edge["source"]), ids.get(edge["target"])
        if not source or not target:
            continue
        style = _EDGE_STYLE + ("dashed=1;" if edge.get("style") == "dotted" else "")
        _cell(root, f"e{e}", "1", value=edge.get("verb") or "", style=style, edge=True,
              source=source, target=target)

    ET.indent(mxfile)
    return ET.tostring(mxfile, encoding="unicode") + "\n"


def write_drawio(graph, all_rows, path, title="AWS architecture"):
    with open(path, "w") as handle:
        handle.write(render_drawio(graph, all_rows, title=title))
