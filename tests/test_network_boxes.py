"""The project-scoped diagram's Region > VPC > subnet boxes."""

import unittest
import xml.etree.ElementTree as ET

from .fakes import audit, make_row

from aws_resource_audit.analyze.architecture import architecture_graph, project_architecture
from aws_resource_audit.analyze.network import network_placement
from aws_resource_audit.present.graph.drawio import render_drawio
from aws_resource_audit.present.graph.network_tree import network_tree


def key(row):
    return audit.member_key(row)


class NetworkFixture(unittest.TestCase):
    def setUp(self):
        self.vpc = make_row("VPC", "vpc-1", "shop-vpc")
        self.public = make_row("Subnet", "subnet-pub", "public-a", description="10.0.0.0/24 us-east-1a")
        self.private = make_row("Subnet", "subnet-priv", "private-a", description="10.0.1.0/24 us-east-1a")
        self.table = make_row("RouteTable", "rtb-main", description="main")
        self.igw = make_row("InternetGateway", "igw-1")
        self.lb = make_row("LoadBalancer", "web")
        self.ec2 = make_row("EC2Instance", "i-app", "app")
        self.fn = make_row("LambdaFunction", "thumbs")
        self.rows = [self.vpc, self.public, self.private, self.table, self.igw,
                     self.lb, self.ec2, self.fn]
        for row in (self.lb, self.ec2, self.fn):
            row["project_id"], row["project_group"] = "tag:shop", "shop"
        links = [(self.public, self.vpc, "subnet.vpc.placement"),
                 (self.private, self.vpc, "subnet.vpc.placement"),
                 (self.table, self.vpc, "routetable.vpc.placement"),
                 (self.table, self.igw, "routetable.internetgateway.route"),
                 (self.lb, self.public, "loadbalancer.subnet.placement"),
                 (self.lb, self.private, "loadbalancer.subnet.placement"),
                 (self.ec2, self.private, "ec2.subnet.placement"),
                 (self.lb, self.ec2, "loadbalancer.any.target")]
        self.scan = {
            "nodes": [{"id": key(r), "kind": "resource", "service": r["service"],
                       "label": r["name"] or r["resource_id"]} for r in self.rows],
            "edges": [{"source": key(s), "target": key(t), "state": "resolved", "category": "link",
                       "links": [{"conn_type": c}]} for s, t, c in links],
        }
        # The private subnet has its own table with no internet route.
        private_table = make_row("RouteTable", "rtb-priv")
        self.rows.append(private_table)
        self.scan["nodes"].append({"id": key(private_table), "kind": "resource",
                                   "service": "RouteTable", "label": "rtb-priv"})
        self.scan["edges"].append({"source": key(private_table), "target": key(self.private),
                                   "state": "resolved", "category": "link",
                                   "links": [{"conn_type": "routetable.subnet.association"}]})
        self.scoped = project_architecture(architecture_graph(self.scan, self.rows),
                                           self.scan, self.rows, "tag:shop")


class PlacementTests(NetworkFixture):
    def test_one_subnet_places_in_it_and_several_place_in_the_vpc(self):
        placement = self.scoped["network"]["placement"]
        self.assertEqual(placement[key(self.ec2)], {"vpc": key(self.vpc), "subnet": key(self.private)})
        self.assertEqual(placement[key(self.lb)], {"vpc": key(self.vpc), "subnet": None})
        self.assertNotIn(key(self.fn), placement)

    def test_public_comes_from_the_main_table_and_private_from_its_own(self):
        net = network_placement(self.scan, self.rows, {key(self.ec2)})
        self.assertFalse(net["subnets"][key(self.private)]["public"])
        both = network_placement(self.scan, self.rows, {key(self.lb)})
        self.assertEqual(both["subnets"], {})    # the balancer spans both, so no subnet box


class RenderTests(NetworkFixture):
    def test_mermaid_nests_region_vpc_and_subnet(self):
        text = audit.render_mermaid(self.scoped, self.rows)
        self.assertIn('["Region us-east-1"]', text)
        self.assertIn('["VPC shop-vpc"]', text)
        self.assertIn('["private-a · private · us-east-1a"]', text)
        self.assertLess(text.index("Region us-east-1"), text.index("VPC shop-vpc"))

    def test_drawio_uses_aws_group_shapes_and_nests_nodes(self):
        cells = ET.fromstring(render_drawio(self.scoped, self.rows)).findall(".//mxCell")
        by_id = {c.get("id"): c for c in cells}
        styles = " ".join(c.get("style") or "" for c in cells)
        for icon in ("group_region", "group_vpc2", "group_security_group"):
            self.assertIn(f"grIcon=mxgraph.aws4.{icon};", styles)
        app = next(c for c in cells if (c.get("value") or "").startswith("app<br>"))
        self.assertIn("private", by_id[app.get("parent")].get("value"))

    def test_the_account_view_keeps_project_boxes(self):
        whole = architecture_graph(self.scan, self.rows)
        self.assertNotIn("Region", audit.render_mermaid(whole, self.rows))


class RegionBoxTests(unittest.TestCase):
    def test_a_bucket_sits_in_its_region_and_cloudfront_in_the_global_box(self):
        """Decision 0062: a bucket has a region, as AWS's own diagrams draw it."""
        bucket = make_row("S3Bucket", "assets", region="eu-west-1")
        cdn = make_row("CloudFrontDistribution", "E1", region="global")
        graph = {"network": {"placement": {}, "vpcs": {}, "subnets": {}},
                 "nodes": [{"id": key(r)} for r in (bucket, cdn)]}
        tree = network_tree(graph, [bucket, cdn])
        self.assertEqual([(b["title"], [n["id"] for n in b["nodes"]]) for b in tree],
                         [("Region eu-west-1", [key(bucket)]),
                          ("AWS global services", [key(cdn)])])


if __name__ == "__main__":
    unittest.main()
