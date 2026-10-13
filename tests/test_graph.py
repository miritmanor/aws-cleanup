"""The dependency-graph payload: report-only links drawn but not grouping, collisions
drawing nothing. Every connection type but the one under test is forced off."""

import json
import unittest

from .fakes import audit, make_row
from .graph_helpers import edge_between, graph_for, node_ids, only, only_these, quiet


class GraphEdgeTests(unittest.TestCase):
    """A resolved/report-only/off/dangling link, as the graph sees it."""

    def _linked_rows(self):
        """An EC2 instance referencing an AMI - one authoritative, on-by-default
        connection type (ec2.ami.image-id) with a real target row."""
        inst = make_row("EC2", "i-0abc", "web")
        ami = make_row("AMI", "ami-0123", "base-image")
        audit.add_edge(inst, "ami-0123", "launched from", "instance ImageId",
                       conn_type="ec2.ami.image-id", target_service="AMI")
        return [inst, ami]

    def test_resolved_edge_becomes_one_directed_graph_edge(self):
        with only("ec2.ami.image-id"):
            rows = self._linked_rows()
            graph, _, _ = graph_for(rows)
        src = audit.member_key(rows[0])
        tgt = audit.member_key(rows[1])
        self.assertEqual(len(graph["edges"]), 1)
        edge = graph["edges"][0]
        self.assertEqual((edge["source"], edge["target"]), (src, tgt))
        self.assertEqual(edge["category"], "link")
        self.assertEqual(edge["state"], "resolved")
        self.assertTrue(edge["grouping"])
        self.assertEqual(edge["links"][0]["conn_type"], "ec2.ami.image-id")
        self.assertEqual(edge["links"][0]["rel"], "launched from")

    def test_report_only_edge_is_drawn_but_never_grouped(self):
        """A report-only link appears in the graph but does not group."""
        with only("ec2.ami.image-id", audit.CONN_REPORT_ONLY):
            rows = self._linked_rows()
            graph, edge_links, _ = graph_for(rows)
        self.assertEqual(edge_links, [], "report-only must not feed grouping")
        self.assertEqual(len(graph["edges"]), 1)
        edge = graph["edges"][0]
        self.assertFalse(edge["grouping"])
        self.assertEqual(edge["category"], "link")
        self.assertEqual(rows[0]["project_group"], "")

    def test_off_type_produces_no_edge_at_all(self):
        with only("ec2.ami.image-id", audit.CONN_OFF):
            rows = self._linked_rows()
            graph, _, _ = graph_for(rows)
        self.assertEqual(graph["edges"], [])
        self.assertEqual(len(graph["nodes"]), 2, "rows are still nodes")

    def test_dangling_edge_emits_a_stub_node(self):
        with only("ec2.ami.image-id"):
            inst = make_row("EC2", "i-0abc", "web")
            audit.add_edge(inst, "ami-gone", "launched from", "instance ImageId",
                           conn_type="ec2.ami.image-id", target_service="AMI")
            rows = [inst]
            graph, _, _ = graph_for(rows)
        stubs = [n for n in graph["nodes"] if n["kind"] == "stub"]
        self.assertEqual(len(stubs), 1)
        self.assertEqual(stubs[0]["label"], "ami-gone")
        self.assertEqual(stubs[0]["service"], "AMI")
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(graph["edges"][0]["state"], "dangling")
        # Nothing was grouped: there is no target row to have merged with, so
        # filtering the export on grouping=true must not return this edge.
        self.assertFalse(graph["edges"][0]["grouping"])
        self.assertEqual(graph["stats"]["dangling_edges"], 1)

    def test_speculative_unresolved_edge_is_silent(self):
        """An unresolved assert_exists=False reference draws no stub."""
        with only("amplify.apigateway.name-match", audit.CONN_ON):
            app = make_row("AmplifyApp", "d123", "myapp")
            audit.add_edge(app, "nope", "possible backend", "name resembles",
                           conn_type="amplify.apigateway.name-match",
                           assert_exists=False)
            rows = [app]
            graph, _, _ = graph_for(rows)
        self.assertEqual(graph["edges"], [])
        self.assertEqual([n for n in graph["nodes"] if n["kind"] == "stub"], [])

    def test_type_collision_produces_no_edge_and_no_stub(self):
        """A wrong-type id match draws nothing: neither an edge nor a stub."""
        with only("ec2.keypair.key-name"):
            inst = make_row("EC2", "i-0abc", "web")
            bucket = make_row("S3Bucket", "shared-name", "shared-name")
            audit.add_edge(inst, "shared-name", "uses key pair", "instance KeyName",
                           conn_type="ec2.keypair.key-name", target_service="KeyPair")
            rows = [inst, bucket]
            graph, _, _ = graph_for(rows)
        self.assertEqual(graph["edges"], [])
        self.assertEqual([n for n in graph["nodes"] if n["kind"] == "stub"], [])

    def test_duplicate_detections_of_one_pair_collapse_to_one_edge(self):
        """Two env-var strategies finding the same Lambda->DynamoDB link is
        routine; drawn as parallel edges they stack into one illegible line."""
        with only_these({"lambda.any.env-var-arn-value": audit.CONN_ON,
                         "lambda.any.env-var-bare-name": audit.CONN_REPORT_ONLY}):
            fn = make_row("Lambda", "myfn", "myfn")
            table = make_row("DynamoDBTable", "mytable", "mytable")
            audit.add_edge(fn, "mytable", "reads/writes", "env var TABLE (arn)",
                           conn_type="lambda.any.env-var-arn-value",
                           target_service="DynamoDBTable")
            audit.add_edge(fn, "mytable", "may use", "env var TABLE (bare name)",
                           conn_type="lambda.any.env-var-bare-name",
                           target_service="DynamoDBTable")
            rows = [fn, table]
            graph, _, _ = graph_for(rows)
        self.assertEqual(len(graph["edges"]), 1)
        edge = graph["edges"][0]
        self.assertEqual(len(edge["links"]), 2)
        self.assertEqual(graph["stats"]["merged"], 1)
        self.assertTrue(edge["grouping"], "one grouping detection is enough")
        self.assertEqual(edge["confidence"], audit.CONF_CONFIG_REFERENCE,
                         "the strongest detection sets the drawn confidence")

    def test_rows_with_no_edges_still_appear_as_nodes(self):
        rows = [make_row("S3Bucket", "lonely", "lonely")]
        graph, _, _ = graph_for(rows)
        self.assertEqual(len(graph["nodes"]), 1)
        self.assertEqual(graph["edges"], [])


class GroupingEdgeTests(unittest.TestCase):
    """Shared-attribute unions are recorded as edges, so a tag cluster is drawn connected."""

    def _tagged_rows(self, n=4):
        return [make_row("S3Bucket", f"b{i}", f"b{i}", tags={"Project": "myapp"})
                for i in range(n)]

    def test_shared_tag_union_becomes_a_star_of_attribute_edges(self):
        with only("group.shared-tag"):
            rows = self._tagged_rows(4)
            graph, _, _ = graph_for(rows)
        attr = [e for e in graph["edges"] if e["category"] == "attribute"]
        # A star, not a clique: 4 rows give 3 edges.
        self.assertEqual(len(attr), 3)
        first = audit.member_key(rows[0])
        self.assertTrue(all(e["source"] == first for e in attr))
        self.assertEqual({e["links"][0]["conn_type"] for e in attr},
                         {"group.shared-tag"})
        self.assertIn("shares project tag", attr[0]["links"][0]["evidence"])

    def test_report_only_grouping_type_draws_nothing(self):
        """For kind="grouping" types report-only and off are equivalent - they
        have no connections text of their own, so there is nothing to report."""
        with only("group.shared-tag", audit.CONN_REPORT_ONLY):
            rows = self._tagged_rows(3)
            graph, _, _ = graph_for(rows)
        self.assertEqual([e for e in graph["edges"] if e["category"] == "attribute"], [])

    def test_edge_endpoints_agree_with_assigned_project_groups(self):
        with only("group.shared-tag"):
            rows = self._tagged_rows(3)
            graph, _, _ = graph_for(rows)
        group_by_key = {audit.member_key(r): r["project_group"] for r in rows}
        for e in graph["edges"]:
            self.assertEqual(group_by_key[e["source"]], group_by_key[e["target"]])
            self.assertTrue(group_by_key[e["source"]])

    def test_a_real_link_outranks_an_attribute_union_for_the_same_pair(self):
        with only_these({"ec2.ami.image-id": audit.CONN_ON,
                         "group.shared-tag": audit.CONN_ON}):
            inst = make_row("EC2", "i-0abc", "web", tags={"Project": "myapp"})
            ami = make_row("AMI", "ami-0123", "base", tags={"Project": "myapp"})
            audit.add_edge(inst, "ami-0123", "launched from", "instance ImageId",
                           conn_type="ec2.ami.image-id", target_service="AMI")
            rows = [inst, ami]
            graph, _, _ = graph_for(rows)
        self.assertEqual(len(graph["edges"]), 1)
        edge = graph["edges"][0]
        self.assertEqual(edge["category"], "link",
                         "a real link explains the pair better than a shared tag")
        self.assertEqual(len(edge["links"]), 2, "but the tag reason is kept")


class GraphInvariantTests(unittest.TestCase):
    """Structural guards - cheap, and they catch the class of bug where the
    payload is subtly unusable rather than visibly wrong."""

    def _mixed_graph(self):
        with only("ec2.ami.image-id"):
            inst = make_row("EC2", "i-0abc", "web")
            ami = make_row("AMI", "ami-0123", "base")
            sg = make_row("SecurityGroup", "sg-01", "ssh from home")
            audit.add_edge(inst, "ami-0123", "launched from", "instance ImageId",
                           conn_type="ec2.ami.image-id", target_service="AMI")
            audit.add_edge(inst, "ami-gone", "launched from", "instance ImageId",
                           conn_type="ec2.ami.image-id", target_service="AMI")
            rows = [inst, ami, sg]
            graph, _, _ = graph_for(rows)
        return rows, graph

    def test_every_edge_endpoint_exists_as_a_node(self):
        _, graph = self._mixed_graph()
        ids = node_ids(graph)
        for e in graph["edges"]:
            self.assertIn(e["source"], ids)
            self.assertIn(e["target"], ids)

    def test_node_ids_are_unique(self):
        _, graph = self._mixed_graph()
        ids = [n["id"] for n in graph["nodes"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_resource_node_ids_are_member_keys(self):
        """Node ids equal member_key, which the HTML page recomputes in JS."""
        rows, graph = self._mixed_graph()
        resource_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "resource"}
        self.assertEqual(resource_ids, {audit.member_key(r) for r in rows})

    def test_payload_is_plain_json(self):
        """It lands in a <script> block, so it cannot rely on a default=str."""
        _, graph = self._mixed_graph()
        json.dumps(graph)

    def test_shared_plumbing_nodes_are_flagged_as_hubs(self):
        """The page hides high-degree hubs by default; the policy of which
        services those are lives in NON_BRIDGING_SERVICES, not in the JS."""
        _, graph = self._mixed_graph()
        by_id = {n["id"]: n for n in graph["nodes"]}
        hubs = [n for n in by_id.values() if n["hub"]]
        self.assertEqual([n["service"] for n in hubs], ["SecurityGroup"])


class GraphRowContractTests(unittest.TestCase):
    """row["_graph"], the record resolve_edges leaves for the graph, survives the pipeline
    and graph building."""

    def _linked_rows(self):
        inst = make_row("EC2", "i-0abc", "web")
        ami = make_row("AMI", "ami-0123", "base")
        audit.add_edge(inst, "ami-0123", "launched from", "instance ImageId",
                       conn_type="ec2.ami.image-id", target_service="AMI")
        return [inst, ami]

    def test_new_row_declares_the_key(self):
        """Declared in the schema beside "_edges", not conjured mid-pipeline."""
        row = make_row("EC2", "i-0abc", "web")
        self.assertEqual(row["_references"], [])

    def test_grouping_preserves_the_graph_records(self):
        with only("ec2.ami.image-id"):
            rows = self._linked_rows()
            links = audit.resolve_edges(rows)
            quiet(audit.apply_project_grouping, rows, edge_links=links)
        for row in rows:
            self.assertIn("_references", row, "grouping dropped the reference records")

    def test_build_graph_data_is_idempotent(self):
        """Building the graph twice gives the same graph."""
        with only("ec2.ami.image-id"):
            rows = self._linked_rows()
            graph, _, _ = graph_for(rows)
            self.assertTrue(graph["edges"])
            again = audit.build_graph_data(rows)
        self.assertEqual(again["edges"], graph["edges"])
        self.assertEqual(again["nodes"], graph["nodes"])
