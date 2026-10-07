// The dependency graph, a port of present/graph/cytoscape.py. Edge styles carry meaning
// (graph/style.ts); node colours come from GET /api/service-meta, never a copy here.

import { useEffect, useMemo, useRef, useState } from "react";
import cytoscape from "cytoscape";
import type { Core, ElementDefinition } from "cytoscape";
import type { Graph, GraphEdgeLink, Row, ServiceMeta } from "../api/types";
import { rowKey } from "../rows";
import { LAYOUTS, runLayout } from "../graph/layout";
import type { LayoutChoice } from "../graph/layout";
import { STYLE } from "../graph/style";

const TIER_LABEL: Record<string, string> = {
  runtime: "runtime",
  deployment: "deployment",
  "": "unclassified",
};

// Matches LABEL_BUDGET in present/graph/labels.py; the ellipsis goes in the middle.
const LABEL_BUDGET = 42;

function shortLabel(value: string): string {
  const s = String(value ?? "");
  if (s.length <= LABEL_BUDGET) return s;
  const head = Math.ceil((LABEL_BUDGET - 1) * 0.55);
  return s.slice(0, head) + "…" + s.slice(s.length - (LABEL_BUDGET - 1 - head));
}

interface Props {
  graph: Graph;
  rows: Row[];
  serviceMeta: ServiceMeta;
  allNodes: boolean;
  onAllNodesChange: (value: boolean) => void;
  /** Clicking a node filters the table to it, joined on member_key. */
  onPickNode: (key: string | null) => void;
}

export function GraphPanel({
  graph, rows, serviceMeta, allNodes, onAllNodesChange, onPickNode,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const [scope, setScope] = useState("");
  const [showReportOnly, setShowReportOnly] = useState(true);
  const [showAttribute, setShowAttribute] = useState(true);
  const [hideIsolated, setHideIsolated] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [selectedEdgeIdx, setSelectedEdgeIdx] = useState<number | null>(null);
  // The tapped node plus its one-hop neighbourhood, or null: restricts what the
  // canvas draws (unlike `selected`), like cytoscape.py's focusOn().
  const [focus, setFocus] = useState<Set<string> | null>(null);
  const [layout, setLayout] = useState<LayoutChoice>("clusters");
  const [fullscreen, setFullscreen] = useState(false);

  // The colour legend: service-meta categories actually present in this scan.
  const colorLegend = useMemo(() => {
    const seen = new Map<string, string>();
    for (const row of rows) {
      const meta = serviceMeta[String(row.service)];
      if (meta && !seen.has(meta.category)) seen.set(meta.category, meta.color);
    }
    return [...seen.entries()];
  }, [rows, serviceMeta]);

  const rowByKey = useMemo(
    () => new Map(rows.map((r) => [rowKey(r), r])),
    [rows],
  );

  // [project_id, name] for every project with a drawn node. Boxes key on the ID.
  const groups = useMemo(() => {
    const found = new Map<string, string>();
    for (const node of graph.nodes) {
      const row = rowByKey.get(node.id);
      const pid = row?.project_id ? String(row.project_id) : "";
      if (pid) found.set(pid, String(row?.project_group || pid));
    }
    return [...found.entries()].sort((a, b) => a[1].localeCompare(b[1]) || a[0].localeCompare(b[0]));
  }, [graph, rowByKey]);

  // Tiers draw as compartments inside a project box, never as two projects, and only
  // when a project holds more than one tier.
  const tiersInGroup = useMemo(() => {
    const found = new Map<string, Set<string>>();
    for (const node of graph.nodes) {
      const row = rowByKey.get(node.id);
      const group = row?.project_id ? String(row.project_id) : "";
      if (!group) continue;
      if (!found.has(group)) found.set(group, new Set());
      found.get(group)!.add(String(row?.tier ?? ""));
    }
    return found;
  }, [graph, rowByKey]);

  // Tier first: the tier vocabulary is fixed and colon-free, so this is unique
  // per (project, tier) whatever a project is called.
  const tierBoxId = (group: string, tier: string) =>
    `tier:${tier || "none"}:${group}`;

  const elements = useMemo<ElementDefinition[]>(() => {
    const els: ElementDefinition[] = [];
    // Projects become compound parents. Nodes in no project get NO parent: one
    // "(no project)" box would assert a relatedness grouping refused to claim.
    for (const [g, name] of groups) {
      // `group` (a project_id) is on BOTH box levels, so the scope filter compares one field.
      els.push({ data: { id: `grp:${g}`, label: name, isGroup: true, group: g } });
      const tiers = tiersInGroup.get(g);
      if (!tiers || tiers.size < 2) continue;
      for (const tier of [...tiers].sort()) {
        els.push({ data: {
          id: tierBoxId(g, tier), label: TIER_LABEL[tier] ?? tier,
          isGroup: true, isTier: true, group: g, parent: `grp:${g}`,
        } });
      }
    }
    for (const node of graph.nodes) {
      const row = rowByKey.get(node.id);
      const group = row?.project_id ? String(row.project_id) : "";
      const tier = String(row?.tier ?? "");
      const split = (tiersInGroup.get(group)?.size ?? 0) > 1;
      const full = node.label || node.id;
      // A second line naming the service: there is no hover tooltip, and colour only
      // narrows a node to a category.
      const label = node.service ? `${shortLabel(full)}\n${node.service}` : shortLabel(full);
      els.push({ data: {
        id: node.id, label, full, kind: node.kind,
        service: node.service, hub: !!node.hub, tier,
        parent: !group ? undefined
          : split ? tierBoxId(group, tier) : `grp:${group}`,
        color: serviceMeta[node.service]?.color ?? "#8fa3b8",
        risk: row ? String(row.risk_if_removed ?? "") : "",
      } });
    }
    graph.edges.forEach((edge, i) => {
      els.push({ data: {
        id: `e${i}`, source: edge.source, target: edge.target,
        category: edge.category, state: edge.state,
        grouping: !!edge.grouping, bridged: !!edge.bridged,
      } });
    });
    return els;
  }, [graph, groups, rowByKey, serviceMeta, tiersInGroup]);

  // Build once per payload. Rebuilding on every filter change would reset the
  // layout, and a graph that re-scatters when you tick a checkbox is unusable.
  useEffect(() => {
    if (!container.current) return;
    const cy = cytoscape({
      container: container.current,
      elements,
      style: STYLE,
      wheelSensitivity: 0.2,
    });
    cyRef.current = cy;

    cy.on("tap", "node", (evt) => {
      const node = evt.target;
      if (node.data("isGroup")) return;
      cy.elements().removeClass("picked");
      node.addClass("picked");
      setSelected(node.id());
      setSelectedEdgeIdx(null);
      onPickNode(node.id());
      const keys = new Set<string>([node.id()]);
      node.neighborhood("node").forEach((n: cytoscape.NodeSingular) => {
        if (!n.data("isGroup")) keys.add(n.id());
      });
      setFocus(keys);
    });
    cy.on("tap", "edge", (evt) => {
      cy.elements().removeClass("picked");
      evt.target.addClass("picked");
      setSelected(null);
      setSelectedEdgeIdx(Number(evt.target.id().slice(1)));
    });
    cy.on("tap", (evt) => {
      if (evt.target === cy) {
        cy.elements().removeClass("picked");
        setSelected(null);
        setSelectedEdgeIdx(null);
        onPickNode(null);
        setFocus(null);
      }
    });

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [elements, onPickNode]);

  // Filters and layout. Separate from construction so ticking a box re-runs
  // the layout over what is visible without discarding the graph.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;

    cy.elements().removeClass("hidden");
    cy.edges().forEach((edge) => {
      const attribute = edge.data("category") === "attribute";
      if ((attribute && !showAttribute) ||
          (!attribute && !edge.data("grouping") && !showReportOnly)) {
        edge.addClass("hidden");
      }
    });

    if (scope) {
      // Both box levels carry `group`, so one comparison covers a project box,
      // a tier compartment inside it, and a resource.
      cy.nodes().forEach((node) => {
        const group = node.data("isGroup")
          ? String(node.data("group") ?? "")
          : String(rowByKey.get(node.id())?.project_id ?? "");
        if (group !== scope) node.addClass("hidden");
      });
    }

    // A focused resource restricts the canvas the same way scope does.
    if (focus) {
      cy.nodes().forEach((node) => {
        if (node.data("isGroup")) return;
        if (!focus.has(node.id())) node.addClass("hidden");
      });
    }

    // Hide edges whose endpoint is hidden: Cytoscape's bezier bundling crashes on an
    // edge pointing at an unrendered node.
    cy.edges().forEach((edge) => {
      if (edge.source().hasClass("hidden") || edge.target().hasClass("hidden")) {
        edge.addClass("hidden");
      }
    });

    if (hideIsolated) {
      cy.nodes().forEach((node) => {
        if (node.data("isGroup") || node.hasClass("hidden")) return;
        const live = node.connectedEdges().filter((e) => !e.hasClass("hidden"));
        if (live.length === 0) node.addClass("hidden");
      });
    }

    // Hide emptied group boxes, innermost (tier compartments) first.
    cy.nodes("[?isTier]").forEach((node) => {
      if (node.children().filter((c) => !c.hasClass("hidden")).length === 0) {
        node.addClass("hidden");
      }
    });
    cy.nodes("[?isGroup][!isTier]").forEach((node) => {
      if (node.children().filter((c) => !c.hasClass("hidden")).length === 0) {
        node.addClass("hidden");
      }
    });

    runLayout(cy, layout);
  }, [scope, showReportOnly, showAttribute, hideIsolated, focus, rowByKey, layout, elements]);

  // Fullscreen is a CSS overlay; Cytoscape must be told about every size change.
  useEffect(() => {
    cyRef.current?.resize();
    document.body.style.overflow = fullscreen ? "hidden" : "";
    return () => { document.body.style.overflow = ""; };
  }, [fullscreen]);

  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFullscreen(false); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const onResize = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => cyRef.current?.resize(), 150);
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      if (timer) clearTimeout(timer);
    };
  }, []);

  const clearFocus = () => {
    cyRef.current?.elements().removeClass("picked");
    setSelected(null);
    setSelectedEdgeIdx(null);
    setFocus(null);
    onPickNode(null);
  };

  const selectedRow = selected ? rowByKey.get(selected) : undefined;
  const selectedNode = selected
    ? graph.nodes.find((n) => n.id === selected)
    : undefined;

  const nodeById = useMemo(
    () => new Map(graph.nodes.map((n) => [n.id, n])),
    [graph],
  );
  const nameOf = (id: string) => nodeById.get(id)?.label || id;
  const selectedEdge = selectedEdgeIdx !== null ? graph.edges[selectedEdgeIdx] : undefined;
  const uniqLinks = (f: (l: GraphEdgeLink) => string) =>
    selectedEdge ? [...new Set(selectedEdge.links.map(f).filter(Boolean))].join(" / ") : "";

  return (
    <section className={fullscreen ? "graph-panel fullscreen" : "graph-panel"}>
      <div className="graph-color-legend">
        {colorLegend.map(([category, color]) => (
          <span key={category}><i className="dot" style={{ background: color }} />{category}</span>
        ))}
      </div>
      <div className="graph-controls">
        <select value={scope} onChange={(e) => setScope(e.target.value)}>
          <option value="">(all projects)</option>
          {groups.map(([g, name]) => (
            <option key={g} value={g}>{name}</option>
          ))}
        </select>
        <select
          value={layout}
          title="How the nodes are placed"
          onChange={(e) => setLayout(e.target.value as LayoutChoice)}
        >
          {LAYOUTS.map((l) => (
            <option key={l.id} value={l.id}>{l.label}</option>
          ))}
        </select>
        <label className="inline">
          <input type="checkbox" checked={showReportOnly}
                 onChange={(e) => setShowReportOnly(e.target.checked)} />
          Report-only links
        </label>
        <label className="inline">
          <input type="checkbox" checked={showAttribute}
                 onChange={(e) => setShowAttribute(e.target.checked)} />
          Shared attributes
        </label>
        <label className="inline">
          <input type="checkbox" checked={hideIsolated}
                 onChange={(e) => setHideIsolated(e.target.checked)} />
          Hide isolated
        </label>
        <label className="inline" title="Draw supporting resources (IAM roles, log groups, security groups) instead of contracting them away">
          <input type="checkbox" checked={allNodes}
                 onChange={(e) => onAllNodesChange(e.target.checked)} />
          Every resource
        </label>
        <span className="count">
          {graph.stats.nodes} resources, {graph.stats.edges} links
          {graph.stats.hidden_nodes ? (
            <> · {graph.stats.hidden_nodes} contracted away</>
          ) : null}
        </span>
        {focus && (
          <span className="count focus-chip">
            Showing {nodeById.get(selected ?? "")?.label || selected}
            {focus.size > 1 ? ` and its ${focus.size - 1} neighbour${focus.size - 1 === 1 ? "" : "s"}` : " (no neighbours)"}
            <button type="button" className="fullscreen-btn" onClick={clearFocus}>Clear focus</button>
          </span>
        )}
        <button type="button" className="fullscreen-btn" onClick={() => setFullscreen((v) => !v)}>
          {fullscreen ? "Exit fullscreen" : "Fullscreen"}
        </button>
      </div>

      <div className="graph-body">
        <div className="graph-canvas" ref={container} />
        <aside className="graph-inspector">
          {selectedRow ? (
            <>
              <div className="ins-title">
                {String(selectedRow.name || selectedRow.resource_id)}
              </div>
              <dl>
                <dt>Resource</dt>
                <dd className="mono">
                  {selectedRow.console_url ? (
                    <a href={String(selectedRow.console_url)} target="_blank" rel="noreferrer">
                      {String(selectedRow.resource_id)}
                    </a>
                  ) : String(selectedRow.resource_id)}
                </dd>
                <dt>Service</dt><dd>{String(selectedRow.service)}</dd>
                <dt>Region</dt><dd>{String(selectedRow.region)}</dd>
                <dt>Project</dt>
                <dd>{String(selectedRow.project_group || "(no project)")}</dd>
                <dt>Why grouped</dt><dd>{String(selectedRow.why_grouped || "—")}</dd>
                <dt>Runtime / deployment</dt>
                <dd>{String(selectedRow.tier || "unclassified")}</dd>
                {selectedRow.why_tier ? (
                  <><dt>Why</dt><dd>{String(selectedRow.why_tier)}</dd></>
                ) : null}
                <dt>Flag</dt><dd>{String(selectedRow.flag || "—")}</dd>
                <dt>Risk if removed</dt><dd>{String(selectedRow.risk_if_removed || "—")}</dd>
                <dt>Cost, last 30d</dt>
                <dd>{String(selectedRow.est_monthly_cost_usd || "—")}
                  {selectedRow.cost ? ` (${String(selectedRow.cost)})` : ""}</dd>
                <dt>Connections</dt><dd>{String(selectedRow.connections || "—")}</dd>
              </dl>
            </>
          ) : selectedNode ? (
            <>
              <div className="ins-title">{selectedNode.label || selectedNode.id}</div>
              <dl>
                <dt>Status</dt>
                <dd>
                  Not found in this scan — referenced by something that is. It
                  may be deleted, or live in a region or account this run did
                  not cover.
                </dd>
                <dt>Expected type</dt><dd>{selectedNode.service || "unknown"}</dd>
              </dl>
            </>
          ) : selectedEdge ? (
            <>
              <div className="ins-title">
                {nameOf(selectedEdge.source)} &rarr; {nameOf(selectedEdge.target)}
                {selectedEdge.links.length > 1 ? ` (${selectedEdge.links.length} detections)` : ""}
              </div>
              <dl>
                <dt>From</dt><dd>{nameOf(selectedEdge.source)}</dd>
                <dt>To</dt><dd>{nameOf(selectedEdge.target)}</dd>
                <dt>Relationship</dt><dd>{uniqLinks((l) => l.rel) || "—"}</dd>
                <dt>Evidence</dt><dd>{uniqLinks((l) => l.evidence) || "—"}</dd>
                <dt>Detected by</dt><dd>{uniqLinks((l) => l.conn_type) || "—"}</dd>
                <dt>Confidence</dt><dd>{selectedEdge.confidence}</dd>
                <dt>Used for grouping</dt>
                <dd>{selectedEdge.grouping ? "yes" : "no — report only"}</dd>
                {selectedEdge.bridged && selectedEdge.via?.length ? (
                  <>
                    <dt>Inferred through</dt>
                    <dd>
                      {selectedEdge.via.map((v) => `${v.service} ${v.label}`).join(", ")}
                      {" "}— not drawn as nodes. Enable "Every resource" to see them.
                    </dd>
                  </>
                ) : null}
              </dl>
            </>
          ) : (
            <p className="empty">
              Click a node to inspect it, or an edge to see the link between them.
              Clicking a node also narrows the canvas and the table below to it and its
              neighbours - clear that with the chip above once it appears.
            </p>
          )}
        </aside>
      </div>

      <div className="graph-legend">
        <span><i className="ln solid" /> grouped link</span>
        <span><i className="ln dashed" /> report-only</span>
        <span><i className="ln dotted" /> shared attribute</span>
        <span><i className="ln bridged" /> bridged through a hidden resource</span>
        <span><i className="dot ghost" /> not in this scan</span>
      </div>
    </section>
  );
}
