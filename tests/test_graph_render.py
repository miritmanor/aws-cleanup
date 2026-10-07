"""Rendering the graph: the Mermaid architecture view, the HTML report's graph and
diagram tabs, graph_all_nodes, and one project's extracted subgraph."""

import contextlib
import io
import json
import os
import re
import tempfile
import unittest

from . import corpus
from .fakes import audit, make_row
from .graph_helpers import graph_for, only, only_these


class MermaidArchitectureTests(unittest.TestCase):
    """render_mermaid() is the architecture view: the Diagram tab and the .mmd
    file are the same string. The node and line rules are in test_architecture.py."""

    def _graph(self):
        with only("ec2.ami.image-id"):
            inst = make_row("EC2Instance", "i-0abc", "web")
            lb = make_row("LoadBalancer", "lb-01", "front-door")
            audit.add_edge(inst, "lb-01", "behind", "target group",
                           conn_type="ec2.ami.image-id", target_service="LoadBalancer")
            rows = [inst, lb]
            graph, _, _ = graph_for(rows)
        return rows, audit.contract_graph(graph)

    def test_no_admin_subgraph_is_drawn(self):
        rows, graph = self._graph()
        text = audit.render_mermaid(graph, rows)
        self.assertNotIn("admingraph", text)
        self.assertNotIn("Administrative resources", text)


class HtmlReportTests(unittest.TestCase):
    """The report template's doubled braces must survive .format() in tests, not at the
    end of a real scan."""

    def _rows_and_graph(self):
        with only("ec2.ami.image-id"):
            inst = make_row("EC2", "i-0abc", "web")
            ami = make_row("AMI", "ami-0123", "base")
            audit.add_edge(inst, "ami-0123", "launched from", "instance ImageId",
                           conn_type="ec2.ami.image-id", target_service="AMI")
            rows = [inst, ami]
            graph, _, _ = graph_for(rows)
        return rows, graph

    def test_renders_with_a_graph(self):
        rows, graph = self._rows_and_graph()
        html = audit.render_html_report(rows, graph=graph)
        self.assertIn("const GRAPH = {", html)
        self.assertIn(f"cytoscape@{audit.CYTOSCAPE_VERSION}", html)
        self.assertIn('id="viewSwitch"', html)
        self.assertIn('id="graphWrap"', html)
        self.assertIn("new Tabulator", html)

    def test_renders_without_a_graph(self):
        """graph=None must drop the panel AND its CDN script, not just hide it."""
        rows, _ = self._rows_and_graph()
        html = audit.render_html_report(rows, graph=None)
        self.assertIn("const GRAPH = null;", html)
        self.assertNotIn("cytoscape", html)
        self.assertNotIn('id="viewSwitch"', html)
        self.assertIn("new Tabulator", html)

    def test_every_row_is_embedded(self):
        rows, graph = self._rows_and_graph()
        html = audit.render_html_report(rows, graph=graph)
        start = html.index("const ROWS = ") + len("const ROWS = ")
        end = html.index(";\nconst SERVICE_META", start)
        self.assertEqual(len(json.loads(html[start:end])), len(rows))

    def test_graph_js_and_css_survive_formatting_verbatim(self):
        """GRAPH_JS/GRAPH_CSS are format VALUES with single braces, embedded verbatim."""
        rows, graph = self._rows_and_graph()
        html = audit.render_html_report(rows, graph=graph)
        self.assertIn(audit.GRAPH_JS, html)
        self.assertIn(audit.GRAPH_CSS, html)
        self.assertIn(audit.GRAPH_PANEL, html)

    def _full_and_contracted(self):
        """Lambda -> IAMRole -> S3Bucket: the role is in `full`, contracted in `contracted`."""
        with only_these({"iamrole.any.resource-grant": audit.CONN_ON,
                         "group.shared-iam-role": audit.CONN_ON}):
            fn = make_row("LambdaFunction", "myFn", "myFn", iam_role="execRole")
            role = make_row("IAMRole", "execRole", "execRole", iam_role="execRole")
            bucket = make_row("S3Bucket", "bucket-0", "bucket-0")
            audit.add_edge(role, "bucket-0", "grants access to",
                           "Resource ARN in the role's own policies",
                           conn_type="iamrole.any.resource-grant",
                           target_service="S3Bucket")
            rows = [fn, role, bucket]
            full, _, _ = graph_for(rows)
        return rows, full, audit.contract_graph(full)

    def test_diagram_tab_can_differ_from_the_graph_tab(self):
        """With graph_all_nodes, the Graph tab draws everything while the Diagram tab
        stays the contracted architecture view."""
        rows, full, contracted = self._full_and_contracted()
        html = audit.render_html_report(rows, graph=full, diagram_graph=contracted)
        m = re.search(r"const GRAPH = (.*?);\n", html)
        self.assertEqual(len(json.loads(m.group(1))["nodes"]), len(full["nodes"]))
        # One escaped <br/> per node of the CONTRACTED graph.
        self.assertEqual(html.count("&lt;br/&gt;"), len(contracted["nodes"]))

    def test_diagram_graph_defaults_to_the_graph_tab(self):
        """diagram_graph=None falls back to `graph`."""
        rows, graph = self._rows_and_graph()
        html = audit.render_html_report(rows, graph=graph)
        self.assertEqual(html.count("&lt;br/&gt;"), len(graph["nodes"]))


class RenderOutputsGraphAllNodesTests(unittest.TestCase):
    """render_outputs(): graph_all_nodes reaches the Graph tab and the JSON, never the
    Diagram tab or the .mmd file."""

    def test_the_mmd_file_and_the_diagram_tab_draw_the_architecture_graph(self):
        rows, contracted, full, notes = corpus.build()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "aws_scan.json")
            audit.write_snapshot(path, rows, full, notes, account="123456789012",
                                 regions=["us-east-1"], scanned_at=audit.now())
            snapshot = audit.read_snapshot(path)
            settings = audit.Settings(output_dir=tmp, graph_all_nodes=True)
            paths = audit.output_paths(settings)
            with contextlib.redirect_stdout(io.StringIO()):
                audit.render_outputs(snapshot, paths, settings)

            with open(paths["html"]) as f:
                html = f.read()
            with open(paths["mermaid"]) as f:
                mmd = f.read()

        m = re.search(r"const GRAPH = (.*?);\n", html)
        graph_node_count = len(json.loads(m.group(1))["nodes"])
        mmd_node_count = mmd.count("<br/>")

        # The whole point: with graph_all_nodes on, the Graph tab is bigger -
        # if it were not, this test would be proving nothing.
        self.assertGreater(graph_node_count, mmd_node_count)
        self.assertEqual(mmd_node_count,
                         len(audit.architecture_graph(full, snapshot.rows)["nodes"]))
        self.assertEqual(html.count("&lt;br/&gt;"), mmd_node_count,
                         "the in-page Diagram tab must match the .mmd file exactly")


class ExtractProjectTests(unittest.TestCase):
    """One project's subgraph plus one hop of context: owned on project_id (never the
    label), neighbours kept as context, never counted."""

    def _rows(self):
        rows = []
        for rid, project in (("a", "p1"), ("b", "p1"), ("c", "p2"), ("d", "")):
            row = make_row("LambdaFunction", rid, rid)
            row["project_id"] = project
            row["project_group"] = {"p1": "One", "p2": "Two"}.get(project, "")
            rows.append(row)
        return rows

    def _graph(self, rows, pairs):
        return {
            "nodes": [{"id": audit.member_key(r), "label": r["name"],
                       "service": r["service"], "kind": "resource"} for r in rows],
            "edges": [{"source": audit.member_key(rows[i]),
                       "target": audit.member_key(rows[j]),
                       "category": "link", "state": "on", "grouping": True,
                       "links": []} for i, j in pairs],
        }

    def test_it_keeps_what_the_project_owns(self):
        rows = self._rows()
        out = audit.extract_project(self._graph(rows, [(0, 1)]), rows, "p1")
        self.assertEqual({n["id"] for n in out["nodes"]},
                         {audit.member_key(rows[0]), audit.member_key(rows[1])})
        self.assertFalse(any(n["context"] for n in out["nodes"]))

    def test_a_neighbour_in_another_project_is_kept_as_context(self):
        rows = self._rows()
        out = audit.extract_project(self._graph(rows, [(0, 2)]), rows, "p1")
        by_id = {n["id"]: n for n in out["nodes"]}
        self.assertFalse(by_id[audit.member_key(rows[0])]["context"])
        self.assertTrue(by_id[audit.member_key(rows[2])]["context"])

    def test_context_is_one_hop_only(self):
        """Two hops out reaches the whole account through shared plumbing, which
        is the thing a project view exists to avoid."""
        rows = self._rows()
        # a -> c -> d. `d` is two hops from the project and must not appear.
        out = audit.extract_project(self._graph(rows, [(0, 2), (2, 3)]), rows, "p1")
        self.assertNotIn(audit.member_key(rows[3]), {n["id"] for n in out["nodes"]})

    def test_an_unrelated_node_is_left_out_entirely(self):
        rows = self._rows()
        out = audit.extract_project(self._graph(rows, [(0, 1)]), rows, "p1")
        self.assertNotIn(audit.member_key(rows[3]), {n["id"] for n in out["nodes"]})

    def test_ownership_is_matched_on_the_id_not_the_label(self):
        """Two projects sharing a display name must not merge into one diagram."""
        rows = self._rows()
        rows[2]["project_group"] = "One"          # same label, different id
        out = audit.extract_project(self._graph(rows, [(0, 1)]), rows, "p1")
        self.assertNotIn(audit.member_key(rows[2]), {n["id"] for n in out["nodes"]})

    def test_an_unknown_project_extracts_nothing(self):
        rows = self._rows()
        out = audit.extract_project(self._graph(rows, [(0, 1)]), rows, "no-such")
        self.assertEqual(out["nodes"], [])
        self.assertEqual(out["edges"], [])

    def test_the_stats_separate_owned_from_context(self):
        rows = self._rows()
        out = audit.extract_project(self._graph(rows, [(0, 1), (0, 2)]), rows, "p1")
        self.assertEqual(out["stats"]["owned_nodes"], 2)
        self.assertEqual(out["stats"]["context_nodes"], 1)

    def test_a_context_node_is_drawn_outside_the_project_box(self):
        """Mermaid must not put it in the subgraph: the box IS the ownership
        claim, and the grouping pass did not make it for this node."""
        rows = self._rows()
        out = audit.extract_project(self._graph(rows, [(0, 2)]), rows, "p1")
        src = audit.render_mermaid(out, rows)
        body = src.split('subgraph cluster0["One"]')[1].split("    end")[0]
        self.assertIn("a<br/>", body)
        self.assertNotIn("c<br/>", body)



if __name__ == "__main__":
    unittest.main()
