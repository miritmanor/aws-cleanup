"""The architecture graph as an editable draw.io file."""

import unittest
import xml.etree.ElementTree as ET

from .fakes import audit, make_row

from aws_resource_audit.analyze.architecture import ARCHITECTURE_SERVICES, architecture_graph
from aws_resource_audit.present.graph.drawio import DRAWIO_SHAPES, render_drawio
from aws_resource_audit.present.graph.drawio_layout import MAX_ROWS, layout_box


def scan(rows, *links):
    return {
        "nodes": [{"id": audit.member_key(r), "kind": "resource", "service": r["service"],
                   "label": r["name"] or r["resource_id"]} for r in rows],
        "edges": [{"source": audit.member_key(s), "target": audit.member_key(t),
                   "state": "resolved", "category": "link", "links": [{"conn_type": c}]}
                  for s, t, c in links],
    }


def project(rows, pid, name):
    for row in rows:
        row["project_id"], row["project_group"] = pid, name
    return rows


class DrawioTests(unittest.TestCase):
    def setUp(self):
        self.api = make_row("APIGatewayRestApi", "api")
        self.fn = make_row("LambdaFunction", "fn")
        self.role = make_row("IAMRole", "r")
        self.table = make_row("DynamoDBTable", "t")
        self.rows = project([self.api, self.fn, self.role, self.table], "tag:orders", "orders")
        self.arch = architecture_graph(scan(self.rows,
                                            (self.api, self.fn, "apigateway.lambda.integration"),
                                            (self.fn, self.role, "lambda.iamrole.execution-role"),
                                            (self.role, self.table, "iamrole.any.resource-grant")),
                                       self.rows)
        self.cells = ET.fromstring(render_drawio(self.arch, self.rows)).findall(".//mxCell")

    def test_every_node_is_a_draw_io_aws_shape(self):
        icons = [c.get("style") for c in self.cells if "resourceIcon" in (c.get("style") or "")]
        self.assertEqual(len(icons), 3)
        self.assertTrue(any("resIcon=mxgraph.aws4.lambda;" in s for s in icons))
        self.assertTrue(any("resIcon=mxgraph.aws4.api_gateway;" in s for s in icons))

    def test_lines_carry_their_verb_and_grants_are_dashed(self):
        edges = {c.get("value"): c.get("style") for c in self.cells if c.get("edge") == "1"}
        self.assertEqual(set(edges), {"invokes", "can access"})
        self.assertIn("dashed=1;", edges["can access"])
        self.assertNotIn("dashed=1;", edges["invokes"])

    def test_nodes_sit_inside_their_project_box(self):
        by_id = {c.get("id"): c for c in self.cells}
        boxes = [c for c in self.cells if c.get("value") == "orders"]
        self.assertEqual(len(boxes), 1)
        icons = [c for c in self.cells if "resourceIcon" in (c.get("style") or "")]
        self.assertTrue(all(by_id[c.get("parent")] is boxes[0] for c in icons))

    def test_the_same_graph_writes_the_same_file(self):
        self.assertEqual(render_drawio(self.arch, self.rows), render_drawio(self.arch, self.rows))

    def test_columns_run_entry_compute_data(self):
        box = layout_box({"kind": "project", "title": "p", "nodes": self.arch["nodes"],
                          "children": []}, {})
        xs = {n["service"]: x for n, x, _ in box["nodes"]}
        self.assertLess(xs["APIGatewayRestApi"], xs["LambdaFunction"])
        self.assertLess(xs["LambdaFunction"], xs["DynamoDBTable"])

    def test_a_long_column_wraps(self):
        rows = [make_row("S3Bucket", f"b{i}") for i in range(MAX_ROWS + 1)]
        nodes = architecture_graph(scan(rows), rows)["nodes"]
        box = layout_box({"kind": "project", "title": "p", "nodes": nodes, "children": []}, {})
        self.assertEqual(len({x for _, x, _ in box["nodes"]}), 2)

    def test_every_drawn_type_has_a_draw_io_shape(self):
        missing = sorted(ARCHITECTURE_SERVICES - set(DRAWIO_SHAPES))
        self.assertEqual(missing, [], f"add these to DRAWIO_SHAPES in drawio.py: {missing}")

    def test_an_empty_graph_is_still_a_valid_file(self):
        ET.fromstring(render_drawio({"nodes": [], "edges": []}, []))


class GroupBoxTests(unittest.TestCase):
    """An Auto Scaling group is drawn around its instances, inside its EKS cluster."""

    def setUp(self):
        self.eks = make_row("EKSCluster", "kube")
        self.asg = make_row("AutoScalingGroup", "nodes-asg")
        self.i1, self.i2 = make_row("EC2Instance", "i-1", "node-1"), make_row("EC2Instance", "i-2", "node-2")
        self.web = make_row("EC2Instance", "i-web", "web")
        self.rows = project([self.eks, self.asg, self.i1, self.i2, self.web], "tag:k", "k")
        self.arch = architecture_graph(scan(self.rows,
                                            (self.asg, self.i1, "asg.ec2.instance-membership"),
                                            (self.asg, self.i2, "asg.ec2.instance-membership"),
                                            (self.eks, self.asg, "ekscluster.autoscalinggroup.nodegroup")),
                                       self.rows)

    def test_the_graph_carries_the_nested_groups(self):
        kinds = {g["kind"]: g for g in self.arch["groups"]}
        self.assertEqual(kinds["asg"]["members"], [audit.member_key(self.i1), audit.member_key(self.i2)])
        self.assertEqual(kinds["asg"]["parent"], audit.member_key(self.asg).replace(
            "AutoScalingGroup", "EKSCluster").replace("nodes-asg", "kube"))

    def test_mermaid_nests_instances_in_the_group_in_the_cluster(self):
        text = audit.render_mermaid(self.arch, self.rows)
        eks, asg = text.index("EKS cluster kube"), text.index("Auto Scaling group nodes-asg")
        self.assertLess(eks, asg)
        self.assertLess(asg, text.index("node-1<br/>"))
        self.assertGreater(text.index("web<br/>"), text.index("node-2<br/>"))

    def test_drawio_uses_the_auto_scaling_group_shape(self):
        cells = ET.fromstring(render_drawio(self.arch, self.rows)).findall(".//mxCell")
        by_id = {c.get("id"): c for c in cells}
        node = next(c for c in cells if (c.get("value") or "").startswith("node-1<br>"))
        box = by_id[node.get("parent")]
        self.assertIn("grIcon=mxgraph.aws4.group_auto_scaling_group;", box.get("style"))
        self.assertIn("EKS cluster", by_id[box.get("parent")].get("value"))


class EcsClusterBoxTests(unittest.TestCase):
    """An ECS cluster on EC2 capacity is a box around its services and instances,
    holding the Auto Scaling group that supplies them."""

    def setUp(self):
        self.cluster = make_row("ECSCluster", "main")
        self.svc = make_row("ECSService", "main/api", "api")
        self.asg = make_row("AutoScalingGroup", "capacity")
        self.i1, self.i2 = make_row("EC2Instance", "i-1", "node-1"), make_row("EC2Instance", "i-2", "spare")
        self.rows = project([self.cluster, self.svc, self.asg, self.i1, self.i2], "tag:k", "k")
        self.arch = architecture_graph(scan(self.rows,
                                            (self.svc, self.cluster, "ecsservice.ecscluster.membership"),
                                            (self.cluster, self.i1, "ecscluster.ec2instance.container-instance"),
                                            (self.cluster, self.i2, "ecscluster.ec2instance.container-instance"),
                                            (self.asg, self.i1, "asg.ec2.instance-membership")),
                                       self.rows)

    def test_the_cluster_holds_its_services_and_loose_instances_and_the_asg(self):
        kinds = {g["kind"]: g for g in self.arch["groups"]}
        self.assertEqual(sorted(kinds["ecs"]["members"]),
                         sorted([audit.member_key(self.svc), audit.member_key(self.i2)]))
        self.assertEqual(kinds["asg"]["parent"], kinds["ecs"]["id"])

    def test_both_formats_nest_the_asg_inside_the_cluster(self):
        text = audit.render_mermaid(self.arch, self.rows)
        self.assertLess(text.index("ECS cluster main"), text.index("Auto Scaling group capacity"))
        cells = ET.fromstring(render_drawio(self.arch, self.rows)).findall(".//mxCell")
        by_id = {c.get("id"): c for c in cells}
        node = next(c for c in cells if (c.get("value") or "").startswith("node-1<br>"))
        self.assertIn("ECS cluster", by_id[by_id[node.get("parent")].get("parent")].get("value"))


if __name__ == "__main__":
    unittest.main()
