"""Rendering the graph: the Mermaid architecture view, the HTML report's Diagram tab,
and one project's extracted subgraph."""

import contextlib
import io
import json
import os
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
        return rows, graph

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

    def test_renders_with_a_diagram(self):
        rows, graph = self._rows_and_graph()
        html = audit.render_html_report(rows, diagram_graph=graph)
        self.assertIn("mermaid@", html)
        self.assertIn('id="viewSwitch"', html)
        self.assertIn('id="diagramWrap"', html)
        self.assertIn("new Tabulator", html)

    def test_renders_without_a_diagram(self):
        """diagram_graph=None must drop the panel AND its CDN script, not just hide it."""
        rows, _ = self._rows_and_graph()
        html = audit.render_html_report(rows, diagram_graph=None)
        self.assertNotIn("mermaid@", html)
        self.assertNotIn('id="viewSwitch"', html)
        self.assertIn("new Tabulator", html)

    def test_there_is_no_graph_tab(self):
        rows, graph = self._rows_and_graph()
        html = audit.render_html_report(rows, diagram_graph=graph)
        self.assertNotIn("cytoscape", html)
        self.assertNotIn('data-view="graph"', html)

    def test_every_row_is_embedded(self):
        rows, graph = self._rows_and_graph()
        html = audit.render_html_report(rows, diagram_graph=graph)
        start = html.index("const ROWS = ") + len("const ROWS = ")
        end = html.index(";\nconst SERVICE_META", start)
        self.assertEqual(len(json.loads(html[start:end])), len(rows))

    def test_view_js_and_css_survive_formatting_verbatim(self):
        """VIEW_JS/VIEW_CSS are format VALUES with single braces, embedded verbatim."""
        rows, graph = self._rows_and_graph()
        html = audit.render_html_report(rows, diagram_graph=graph)
        self.assertIn(audit.VIEW_JS, html)
        self.assertIn(audit.VIEW_CSS, html)
        self.assertIn(audit.VIEW_SWITCH, html)


class RenderOutputsDiagramTests(unittest.TestCase):
    """render_outputs(): the Diagram tab and the .mmd file draw the same architecture graph."""

    def test_the_mmd_file_and_the_diagram_tab_draw_the_architecture_graph(self):
        rows, full, notes = corpus.build()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "aws_scan.json")
            audit.write_snapshot(path, rows, full, notes, account="123456789012",
                                 regions=["us-east-1"], scanned_at=audit.now())
            snapshot = audit.read_snapshot(path)
            settings = audit.Settings(output_dir=tmp)
            paths = audit.output_paths(settings)
            with contextlib.redirect_stdout(io.StringIO()):
                audit.render_outputs(snapshot, paths, settings)

            with open(paths["html"]) as f:
                html = f.read()
            with open(paths["mermaid"]) as f:
                mmd = f.read()

        mmd_node_count = mmd.count("<br/>")
        self.assertTrue(mmd_node_count, "an empty diagram would prove nothing")
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
