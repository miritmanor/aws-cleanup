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


class GraphContractionTests(unittest.TestCase):
    """contract_graph() keeps STRUCTURAL_SERVICES and marks bridged edges with the hops
    they came from."""

    def _lambda_role_bucket(self, buckets=1, direct=False):
        """A function reaching buckets only through its execution role's grants."""
        types = {"iamrole.any.resource-grant": audit.CONN_ON,
                 "lambda.iamrole.execution-role": audit.CONN_ON}
        if direct:
            types["lambda.any.env-var-arn-value"] = audit.CONN_ON
        with only_these(types):
            fn = make_row("LambdaFunction", "myFn", "myFn", iam_role="execRole")
            role = make_row("IAMRole", "execRole", "execRole", iam_role="execRole")
            audit.add_edge(fn, "execRole", "runs as", "function configuration Role",
                           conn_type="lambda.iamrole.execution-role",
                           target_service="IAMRole")
            rows = [fn, role]
            for i in range(buckets):
                rows.append(make_row("S3Bucket", f"bucket-{i}", f"bucket-{i}"))
                audit.add_edge(role, f"bucket-{i}", "grants access to",
                               "Resource ARN in the role's own policies",
                               conn_type="iamrole.any.resource-grant",
                               target_service="S3Bucket")
                if direct:
                    audit.add_edge(fn, f"bucket-{i}", "references",
                                   "env var value IS an ARN",
                                   conn_type="lambda.any.env-var-arn-value",
                                   target_service="S3Bucket")
            full, _, _ = graph_for(rows)
        return rows, full, audit.contract_graph(full)

    def test_structural_resources_pass_through_untouched(self):
        with only("ebsvolume.ec2.attachment"):
            inst = make_row("EC2Instance", "i-0abc", "web")
            vol = make_row("EBSVolume", "vol-01", "data")
            audit.add_edge(vol, "i-0abc", "attached to", "volume Attachments",
                           conn_type="ebsvolume.ec2.attachment",
                           target_service="EC2Instance")
            full, _, _ = graph_for([vol, inst])
        contracted = audit.contract_graph(full)
        self.assertEqual(contracted["nodes"], full["nodes"])
        self.assertEqual(contracted["edges"], full["edges"])
        self.assertEqual(contracted["stats"]["hidden_nodes"], 0)

    def test_role_between_a_function_and_a_bucket_becomes_one_bridged_edge(self):
        rows, full, contracted = self._lambda_role_bucket()
        fn, role, bucket = (audit.member_key(r) for r in rows)
        self.assertIn(role, node_ids(full), "the full graph still draws the role")
        self.assertNotIn(role, node_ids(contracted))
        edge = edge_between(contracted, fn, bucket)
        self.assertIsNotNone(edge, "the dependency the role proved must survive it")
        self.assertTrue(edge["bridged"])
        self.assertEqual(edge["via"], [{"service": "IAMRole", "label": "execRole"}])
        self.assertIn("via IAMRole execRole", edge["links"][0]["rel"])
        self.assertIn("grants access to", edge["links"][0]["evidence"])
        self.assertTrue(edge["links"][0]["conn_type"].startswith("bridge:"))

    def test_a_bridge_is_only_as_strong_as_its_weaker_half(self):
        """A bridge reports the WEAKEST confidence of the hops it replaced."""
        _, _, contracted = self._lambda_role_bucket()
        edge = contracted["edges"][0]
        self.assertEqual(edge["category"], "link")
        hops = [audit.CONNECTION_TYPES_BY_ID[t].confidence for t in
                ("lambda.iamrole.execution-role", "iamrole.any.resource-grant")]
        weakest = max(hops, key=lambda c: (c == audit.CONF_HEURISTIC,
                                           c == audit.CONF_CONFIG_REFERENCE))
        self.assertEqual(edge["confidence"], weakest)

    def test_a_direct_link_absorbs_the_bridge_for_the_same_pair(self):
        rows, _, contracted = self._lambda_role_bucket(direct=True)
        fn, _, bucket = (audit.member_key(r) for r in rows)
        self.assertEqual(len(contracted["edges"]), 1, "not a parallel edge")
        edge = edge_between(contracted, fn, bucket)
        self.assertNotIn("bridged", edge,
                         "AWS states this link outright - it is not an inference")
        self.assertEqual(len(edge["links"]), 2, "but the bridge's reason is kept")

    def test_a_bridge_is_exactly_one_hop_wide(self):
        """No two-hop bridges: they fuse unrelated plumbing."""
        with only_these({"lambda.loggroup.logging-config": audit.CONN_ON,
                         "iampolicy.any.resource-grant": audit.CONN_ON}):
            fn = make_row("LambdaFunction", "myFn", "myFn")
            logs = make_row("CloudWatchLogGroup", "/aws/lambda/myFn", "/aws/lambda/myFn")
            policy = make_row("IAMPolicy", "AWSLambdaBasicExecutionRole-1", "boilerplate")
            topic = make_row("SNSTopic", "unrelated", "unrelated")
            audit.add_edge(fn, "/aws/lambda/myFn", "logs to",
                           "Lambda LoggingConfig.LogGroup",
                           conn_type="lambda.loggroup.logging-config",
                           target_service="CloudWatchLogGroup")
            for target, service in (("/aws/lambda/myFn", "CloudWatchLogGroup"),
                                    ("unrelated", "SNSTopic")):
                audit.add_edge(policy, target, "grants access to",
                               "Resource ARN in the policy document",
                               conn_type="iampolicy.any.resource-grant",
                               target_service=service)
            full, _, _ = graph_for([fn, logs, policy, topic])
        contracted = audit.contract_graph(full)
        self.assertEqual(len(full["edges"]), 3, "the full graph draws the chain")
        self.assertIsNone(edge_between(contracted, audit.member_key(fn),
                                       audit.member_key(topic)),
                          "a two-hop path through shared boilerplate proves nothing")
        self.assertEqual(contracted["edges"], [])

    def test_a_wide_fan_out_still_bridges(self):
        """Fan-out is not evidence: one role granting many tables still bridges."""
        n = 14
        rows, _, contracted = self._lambda_role_bucket(buckets=n)
        fn = audit.member_key(rows[0])
        self.assertEqual(len(contracted["edges"]), n,
                         "every grant the role proved should survive it")
        for row in rows[2:]:
            edge = edge_between(contracted, fn, audit.member_key(row))
            self.assertIsNotNone(edge, f"{row['resource_id']} lost its bridge")
            self.assertTrue(edge["bridged"])
        self.assertEqual(len(contracted["nodes"]), n + 1, "the rows still exist")

    def test_shared_plumbing_is_removed_without_bridging(self):
        """Two instances sharing one security group are not linked."""
        with only("ec2.securitygroup.membership"):
            a = make_row("EC2Instance", "i-0a", "web")
            b = make_row("EC2Instance", "i-0b", "unrelated")
            sg = make_row("SecurityGroup", "sg-01", "ssh from home")
            for inst in (a, b):
                audit.add_edge(inst, "sg-01", "in security group",
                               "instance SecurityGroups",
                               conn_type="ec2.securitygroup.membership",
                               target_service="SecurityGroup")
            full, _, _ = graph_for([a, b, sg])
        contracted = audit.contract_graph(full)
        self.assertEqual(len(contracted["nodes"]), 2)
        self.assertEqual(contracted["edges"], [])

    def test_stubs_survive_only_when_the_missing_thing_is_structural(self):
        """A ghost node is worth drawing when it names an app resource that
        wasn't scanned; a ghost IAM policy is a dead end with a label."""
        with only_these({"ec2.ami.image-id": audit.CONN_ON,
                         "sns.any.subscription": audit.CONN_ON}):
            inst = make_row("EC2Instance", "i-0abc", "web")
            topic = make_row("SNSTopic", "alerts", "alerts")
            audit.add_edge(inst, "ami-gone", "launched from", "instance ImageId",
                           conn_type="ec2.ami.image-id", target_service="AMI")
            audit.add_edge(topic, "fn-elsewhere", "fans out to",
                           "list_subscriptions_by_topic Endpoint",
                           conn_type="sns.any.subscription",
                           target_service="LambdaFunction")
            full, _, _ = graph_for([inst, topic])
        contracted = audit.contract_graph(full)
        stubs = [n for n in contracted["nodes"] if n["kind"] == "stub"]
        self.assertEqual([n["service"] for n in stubs], ["LambdaFunction"])
        self.assertIsNone(edge_between(contracted, audit.member_key(inst),
                                       "?:AMI:ami-gone"))

    def test_every_edge_endpoint_still_exists_as_a_node(self):
        """The invariant this whole pass most endangers: drop a node and leave
        an edge pointing at it and the page draws a link into nothing."""
        _, _, contracted = self._lambda_role_bucket(buckets=3)
        ids = node_ids(contracted)
        for e in contracted["edges"]:
            self.assertIn(e["source"], ids)
            self.assertIn(e["target"], ids)

    def test_contraction_does_not_mutate_the_full_graph(self):
        """main() prints from one payload and renders from another; --graph-all-
        nodes must not silently receive a half-contracted graph."""
        _, full, _ = self._lambda_role_bucket()
        before = json.dumps(full, sort_keys=True)
        audit.contract_graph(full)
        self.assertEqual(json.dumps(full, sort_keys=True), before)

    def test_payload_is_plain_json(self):
        _, _, contracted = self._lambda_role_bucket()
        json.dumps(contracted)

    def test_stats_describe_the_contracted_graph(self):
        _, full, contracted = self._lambda_role_bucket()
        stats = contracted["stats"]
        self.assertEqual(stats["nodes"], len(contracted["nodes"]))
        self.assertEqual(stats["scanned_nodes"], len(full["nodes"]))
        self.assertEqual(stats["hidden_nodes"], len(full["nodes"]) - stats["nodes"])
        self.assertEqual(stats["bridged_edges"], 1)

    def test_a_contracted_payload_renders_to_html(self):
        rows, _, contracted = self._lambda_role_bucket()
        html = audit.render_html_report(rows, graph=contracted)
        self.assertIn("const GRAPH = {", html)
