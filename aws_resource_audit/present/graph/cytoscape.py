"""Browser assets for the graph panel (Cytoscape JS). Substituted into HTML_TEMPLATE as
format VALUES: braces NOT doubled, never .format() them. Concatenation order matters."""

from .layout import LAYOUT_HEAD
from .panel import GRAPH_CSS, GRAPH_PANEL, GRAPH_SWITCH  # noqa: F401  (re-exported)


# Format VALUES, not template text: braces are NOT doubled - never call .format() on these.
# The graph shows exactly the links the connections column reports; no policy of its own.

CYTOSCAPE_VERSION = "3.30.2"

# Cytoscape, plus whatever the layout engine needs (LAYOUT_HEAD, empty today).
GRAPH_HEAD = (
    f'<script src="https://unpkg.com/cytoscape@{CYTOSCAPE_VERSION}'
    '/dist/cytoscape.min.js"></script>' + LAYOUT_HEAD
)


GRAPH_JS = r"""
// --- Dependency graph: solid = merged its projects, dashed = report-only, dotted = shared
// attribute, thin pale = bridged through an undrawn resource, ghost node = not in this scan.
(function () {
  if (!GRAPH) return;

  const wrap = document.getElementById("graphWrap");
  const tableWrap = document.querySelector(".table-wrap");
  const legend = document.getElementById("graphLegend");
  const inspector = document.getElementById("graphInspector");
  const chip = document.getElementById("focusChip");
  const chipText = document.getElementById("focusChipText");
  const counts = document.getElementById("graphCounts");
  const scopeSel = document.getElementById("graphScope");

  const ROW_BY_KEY = new Map(ROWS.map(r => [rowKey(r), r]));
  const NODE_BY_ID = new Map(GRAPH.nodes.map(n => [n.id, n]));
  let cy = null;

  // Projects become compound parents, keyed by project_id. A
  // node in no project gets no parent: one "(no project)" box would claim a relatedness.
  const groupOf = id => (ROW_BY_KEY.get(id) || {}).project_id || "";
  const PROJECT_NAME = new Map();
  for (const r of ROWS) if (r.project_id) PROJECT_NAME.set(r.project_id, r.project_group || r.project_id);
  const nameOf = g => PROJECT_NAME.get(g) || g;
  const groups = [...new Set(GRAPH.nodes.map(n => groupOf(n.id)).filter(Boolean))]
    .sort((a, b) => nameOf(a).localeCompare(nameOf(b)) || a.localeCompare(b));

  // Tiers draw as compartments inside a project box (never as two projects), and only
  // when the project holds more than one tier.
  const tierOf = id => (ROW_BY_KEY.get(id) || {}).tier || "";
  const TIER_LABEL = {runtime: "runtime", deployment: "deployment", "": "unclassified"};
  const tiersInGroup = new Map();
  for (const n of GRAPH.nodes) {
    const g = groupOf(n.id);
    if (!g) continue;
    if (!tiersInGroup.has(g)) tiersInGroup.set(g, new Set());
    tiersInGroup.get(g).add(tierOf(n.id));
  }
  const isSplit = g => (tiersInGroup.get(g) || new Set()).size > 1;
  // Tier first: the tier vocabulary has no colon, so the id is unique per (project, tier).
  const tierBoxId = (g, tier) => "tier:" + (tier || "none") + ":" + g;
  function parentOf(id) {
    const g = groupOf(id);
    if (!g) return undefined;
    return isSplit(g) ? tierBoxId(g, tierOf(id)) : "grp:" + g;
  }

  scopeSel.innerHTML = '<option value="">(all projects)</option>' +
    groups.map(g => `<option value="${esc(g)}">${esc(nameOf(g))}</option>`).join("");

  // Without "graph_all_nodes" there are no hub nodes, so hide the control for them.
  if (!GRAPH.nodes.some(n => n.hub)) {
    document.getElementById("graphHideHubsLabel").style.display = "none";
  }

  // Likewise: no deployment-tier nodes, no control to hide them.
  if (!GRAPH.nodes.some(n => tierOf(n.id) === "deployment")) {
    document.getElementById("graphHideDeploymentLabel").style.display = "none";
  }

  // Labels are budgeted with a middle ellipsis: AWS names often share long prefixes,
  // so the tail is what tells neighbours apart. The full name is on hover.
  const LABEL_BUDGET = 42;
  function shortLabel(s) {
    s = String(s || "");
    if (s.length <= LABEL_BUDGET) return s;
    const head = Math.ceil((LABEL_BUDGET - 1) * 0.55);
    return s.slice(0, head) + "…" + s.slice(s.length - (LABEL_BUDGET - 1 - head));
  }

  function buildElements() {
    const els = [];
    for (const g of groups) {
      // `group` is carried by BOTH box levels so the scope filter can compare
      // one field instead of asking which kind of box it is looking at.
      els.push({data: {id: "grp:" + g, label: nameOf(g), isGroup: true, group: g}});
      if (!isSplit(g)) continue;
      for (const tier of [...tiersInGroup.get(g)].sort()) {
        els.push({data: {
          id: tierBoxId(g, tier), label: TIER_LABEL[tier] || tier,
          isGroup: true, isTier: true, group: g, parent: "grp:" + g,
        }});
      }
    }
    for (const n of GRAPH.nodes) {
      const row = ROW_BY_KEY.get(n.id);
      const full = n.label || n.id;
      // A second line naming the service (like the Mermaid view); colour alone only
      // narrows it to a category. "\n" is a hard break in Cytoscape.
      const displayLabel = shortLabel(full) + (n.service ? "\n" + n.service : "");
      els.push({data: {
        id: n.id, label: displayLabel, full: full, kind: n.kind, service: n.service,
        hub: !!n.hub, parent: parentOf(n.id), tier: tierOf(n.id),
        color: (SERVICE_META[n.service] || DEFAULT_META).color,
        risk: row ? String(row.risk_if_removed || "") : "",
      }});
    }
    for (let i = 0; i < GRAPH.edges.length; i++) {
      const e = GRAPH.edges[i];
      els.push({data: {
        id: "e" + i, source: e.source, target: e.target,
        category: e.category, state: e.state, grouping: !!e.grouping,
        bridged: !!e.bridged,
        label: (e.links[0] || {}).rel || "",
      }});
    }
    return els;
  }

  const STYLE = [
    // Wrapped, not ellipsised: shortLabel() already decided what fits.
    {selector: "node", style: {
      "background-color": "data(color)", "label": "data(label)", "font-size": 9,
      "color": "#3d4a57", "text-valign": "bottom", "text-margin-y": 3,
      "width": 18, "height": 18, "text-max-width": 108, "text-wrap": "wrap",
      "border-width": 1, "border-color": "rgba(0,0,0,0.25)"}},
    {selector: 'node[risk ^= "HIGH"]', style: {"border-width": 3, "border-color": "#c0392b"}},
    {selector: 'node[kind = "stub"]', style: {
      "background-opacity": 0.12, "background-color": "#9aa7b4",
      "border-style": "dashed", "border-color": "#9aa7b4", "color": "#7b8794"}},
    {selector: "node[?isGroup]", style: {
      "background-opacity": 0.06, "background-color": "#8fa3b8",
      "border-width": 1.5, "border-style": "dashed", "border-color": "#8fa3b8",
      "label": "data(label)", "font-size": 11, "font-weight": "bold",
      "color": "#5b6b7c", "text-valign": "top", "text-halign": "center",
      "padding": 14, "shape": "round-rectangle"}},
    // After the group rule so it wins; quieter than a project box so it never reads as one.
    {selector: "node[?isTier]", style: {
      "background-opacity": 0.05, "border-width": 1, "border-style": "dotted",
      "font-size": 9.5, "font-weight": "normal", "padding": 10}},
    {selector: 'node[?isTier][group][label = "deployment"]', style: {
      "background-color": "#8b7ab8", "border-color": "#8b7ab8", "color": "#6b4fa8"}},
    {selector: 'node[?isTier][group][label = "runtime"]', style: {
      "background-color": "#5f9e77", "border-color": "#5f9e77", "color": "#1c6b39"}},
    {selector: "edge", style: {
      "width": 1.4, "line-color": "#5b6b7c", "target-arrow-color": "#5b6b7c",
      "target-arrow-shape": "triangle", "arrow-scale": 0.7,
      // Bezier so the two arcs of a bidirectional pair stay separately
      // visible - direction here is real information, not decoration.
      "curve-style": "bezier", "opacity": 0.75}},
    {selector: "edge[!grouping]", style: {
      "line-style": "dashed", "line-color": "#b07d2b",
      "target-arrow-color": "#b07d2b"}},
    {selector: 'edge[category = "attribute"]', style: {
      "line-style": "dotted", "line-color": "#9aa7b4", "width": 1,
      "target-arrow-shape": "none", "opacity": 0.5}},
    {selector: 'edge[state = "dangling"]', style: {
      "line-style": "dotted", "line-color": "#9aa7b4",
      "target-arrow-color": "#9aa7b4", "opacity": 0.55}},
    // Last, so it wins: a bridged link must not look like one AWS states directly.
    {selector: "edge[?bridged]", style: {
      "line-style": "solid", "line-color": "#7b93b8", "width": 1,
      "target-arrow-color": "#7b93b8", "opacity": 0.6}},
    {selector: ".hidden", style: {"display": "none"}},
    {selector: ".dimmed", style: {"opacity": 0.15}},
    {selector: ".picked", style: {"border-width": 3, "border-color": "#1668c4"}},
  ];

  function applyGraphFilters() {
    if (!cy) return;
    const scope = scopeSel.value;
    const hideIsolated = document.getElementById("graphHideIsolated").checked;
    const hideHubs = document.getElementById("graphHideHubs").checked;
    const hideDeployment = document.getElementById("graphHideDeployment").checked;
    const showReportOnly = document.getElementById("graphShowReportOnly").checked;
    const showAttr = document.getElementById("graphShowAttr").checked;

    cy.elements().removeClass("hidden");
    cy.edges().forEach(e => {
      const attr = e.data("category") === "attribute";
      if ((attr && !showAttr) || (!attr && !e.data("grouping") && !showReportOnly)) {
        e.addClass("hidden");
      }
    });
    if (scope) {
      // Both box levels carry `group`, so one comparison covers a project box,
      // a tier compartment inside it, and a resource.
      cy.nodes().forEach(n => {
        const g = n.data("isGroup") ? n.data("group") : groupOf(n.id());
        if (g !== scope) n.addClass("hidden");
      });
    }
    // A focused resource restricts the canvas as scope does; graphFocus holds the node
    // plus its one-hop neighbourhood (see focusOn).
    if (graphFocus) {
      cy.nodes().forEach(n => {
        if (n.data("isGroup")) return;
        if (!graphFocus.has(n.id())) n.addClass("hidden");
      });
    }
    // Hidden nodes take their edges with them; emptied boxes are swept up below.
    if (hideDeployment) {
      cy.nodes('[tier = "deployment"]').addClass("hidden");
    }
    // Hub degree is measured here, since scoping can leave a hub with one visible neighbour.
    if (hideHubs) {
      cy.nodes('[?hub]').forEach(n => {
        if (n.connectedEdges(":visible").length > 5) n.addClass("hidden");
      });
    }
    if (hideIsolated) {
      cy.nodes().forEach(n => {
        if (n.data("isGroup") || n.hasClass("hidden")) return;
        if (n.connectedEdges().filter(e => !e.hasClass("hidden")).length === 0) {
          n.addClass("hidden");
        }
      });
    }
    // Hide emptied group boxes, innermost (tier compartments) first.
    cy.nodes("[?isTier]").forEach(n => {
      if (n.children().filter(c => !c.hasClass("hidden")).length === 0) n.addClass("hidden");
    });
    cy.nodes("[?isGroup][!isTier]").forEach(n => {
      if (n.children().filter(c => !c.hasClass("hidden")).length === 0) n.addClass("hidden");
    });

    const vn = cy.nodes().filter(n => !n.hasClass("hidden") && !n.data("isGroup")).length;
    const ve = cy.edges().filter(e => !e.hasClass("hidden")).length;
    counts.textContent = `${vn} of ${GRAPH.stats.nodes} resources, ${ve} of ${GRAPH.stats.edges} links`;
    return vn;
  }

  function runLayout() {
    runGraphLayout(cy, document.getElementById("graphLayout").value);
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g,
      c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  }

  function describe(node) {
    const id = node.id();
    const row = ROW_BY_KEY.get(id);
    const meta = NODE_BY_ID.get(id) || {};
    if (!row) {
      inspector.innerHTML = `<div class="ins-title">${esc(meta.label || id)}</div>
        <dl><dt>Status</dt><dd>Not found in this scan &mdash; referenced by something that is.
        It may be deleted, or live in a region or account this run did not cover.</dd>
        <dt>Expected type</dt><dd>${esc(meta.service || "unknown")}</dd></dl>`;
      return;
    }
    const link = row.console_url
      ? `<a href="${esc(row.console_url)}" target="_blank" rel="noopener">${esc(row.resource_id)}</a>`
      : esc(row.resource_id);
    const rows = [
      ["Resource", link], ["Service", esc(row.service)], ["Region", esc(row.region)],
      ["Project", esc(row.project_group || "(no project)")],
      ["Why grouped", esc(row.why_grouped || "-")],
      ["Runtime / deployment", esc(row.tier || "unclassified")],
      ...(row.why_tier ? [["Why", esc(row.why_tier)]] : []),
      ["Flag", esc(row.flag)], ["Risk if removed", esc(row.risk_if_removed)],
      ["Cost to keep", esc(row.billing)],
      ["Cost, last 30d", esc(row.est_monthly_cost_usd || "-")
                         + (row.cost ? " (" + esc(row.cost) + ")" : "")],
      ["Connections", esc(row.connections || "-")],
    ];
    inspector.innerHTML = `<div class="ins-title">${esc(row.name || row.resource_id)}</div><dl>` +
      rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("") + "</dl>";
  }

  // --- Hover tooltip: answers "what IS this dot"; anything longer is in the inspector.
  const tip = document.getElementById("graphTip");
  const graphEl = document.getElementById("graph");

  function nodeTip(node) {
    if (node.data("isGroup")) {
      // descendants(), not children(): a split project's children are its tier boxes.
      const inside = node.descendants().filter(d => !d.data("isGroup")).length;
      const what = node.data("isTier")
        ? `${esc(node.data("group"))} &mdash; ${esc(node.data("label"))}`
        : "project group";
      return `<b>${esc(node.data("label"))}</b><div class="tip-sub">${what} &mdash; ` +
             `${inside} resources</div>`;
    }
    const row = ROW_BY_KEY.get(node.id());
    const bits = [node.data("service") || "unknown type"];
    if (node.data("kind") === "stub") bits.push("not found in this scan");
    if (row) {
      if (row.region) bits.push(row.region);
      bits.push(row.project_group || "no project");
    }
    return `<b>${esc(node.data("full"))}</b>` +
           `<div class="tip-sub">${bits.map(esc).join(" &middot; ")}</div>`;
  }

  function edgeTip(edge) {
    const e = GRAPH.edges[Number(edge.id().slice(1))];
    if (!e) return "";
    const nameOf = id => esc((NODE_BY_ID.get(id) || {}).label || id);
    const rel = [...new Set(e.links.map(l => l.rel).filter(Boolean))].join(" / ");
    const note = e.bridged ? "inferred through a hidden resource"
      : e.category === "attribute" ? "shared attribute, not a link"
      : e.grouping ? e.confidence : "report only";
    return `<b>${nameOf(e.source)} &rarr; ${nameOf(e.target)}</b>` +
           `<div class="tip-sub">${esc(rel || e.category)} &middot; ${esc(note)}</div>`;
  }

  function placeTip(evt) {
    const p = evt.renderedPosition || {x: 0, y: 0};
    const w = graphEl.clientWidth, h = graphEl.clientHeight;
    let x = p.x + 16, y = p.y + 16;
    if (x + tip.offsetWidth > w) x = Math.max(4, p.x - tip.offsetWidth - 16);
    if (y + tip.offsetHeight > h) y = Math.max(4, p.y - tip.offsetHeight - 16);
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }

  function showTip(evt, html) {
    if (!html) return;
    tip.innerHTML = html;
    tip.style.display = "block";
    placeTip(evt);
  }

  function hideTip() { tip.style.display = "none"; }

  // Edge ids are "e" + payload index, so the full edge record is one lookup away.
  function describeEdge(edge) {
    const e = GRAPH.edges[Number(edge.id().slice(1))];
    if (!e) return;
    const nameOf = id => esc((NODE_BY_ID.get(id) || {}).label || id);
    const uniq = f => [...new Set(e.links.map(f).filter(Boolean))].join(" / ");
    const rows = [
      ["From", nameOf(e.source)], ["To", nameOf(e.target)],
      ["Relationship", esc(uniq(l => l.rel)) || "-"],
      ["Evidence", esc(uniq(l => l.evidence)) || "-"],
      ["Detected by", esc(uniq(l => l.conn_type)) || "-"],
      ["Confidence", esc(e.confidence)],
      ["Used for grouping", e.grouping ? "yes" : "no - report only"],
    ];
    if (e.bridged) {
      rows.push(["Inferred through",
        e.via.map(v => `${esc(v.service)} ${esc(v.label)}`).join(", ") +
        " &mdash; not drawn as nodes. Set \"graph_all_nodes\": true in audit_config.json to see them."]);
    }
    inspector.innerHTML = `<div class="ins-title">${nameOf(e.source)} &rarr; ${nameOf(e.target)}` +
      `${e.links.length > 1 ? ` (${e.links.length} detections)` : ""}</div><dl>` +
      rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("") + "</dl>";
  }

  function focusOn(node) {
    const keys = new Set([node.id()]);
    node.neighborhood("node").forEach(n => { if (!n.data("isGroup")) keys.add(n.id()); });
    graphFocus = keys;
    cy.elements().removeClass("picked");
    node.addClass("picked");
    chip.style.display = "flex";
    const others = keys.size - 1;
    chipText.textContent = `Showing ${node.data("full") || node.data("label")}` +
      (others ? ` and its ${others} neighbour${others === 1 ? "" : "s"}` : " (no neighbours)");
    applyFilters();
    applyGraphFilters();
    // Reuses the computed positions: narrowing is a filter, not a re-layout.
    cy.fit(cy.elements().filter(e => !e.hasClass("hidden")), 40);
    describe(node);
  }

  function clearFocus() {
    graphFocus = null;
    chip.style.display = "none";
    if (cy) cy.elements().removeClass("picked");
    applyFilters();
    applyGraphFilters();
    if (cy) cy.fit(cy.elements().filter(e => !e.hasClass("hidden")), 40);
  }
  document.getElementById("focusChipClear").addEventListener("click", clearFocus);

  function initGraph() {
    if (cy) return;
    if (typeof cytoscape === "undefined") {
      // The rest of the report is unaffected - only this panel needs the CDN.
      document.getElementById("graph").innerHTML =
        '<div class="graph-note">The dependency graph needs the Cytoscape library, which is ' +
        'loaded from unpkg.com and could not be fetched. The table view works offline; ' +
        're-open this page with network access to see the graph.</div>';
      return;
    }
    cy = cytoscape({
      container: document.getElementById("graph"),
      elements: buildElements(),
      style: STYLE,
      wheelSensitivity: 0.2,
    });
    cy.on("tap", "node", function (evt) {
      if (evt.target.data("isGroup")) return;
      focusOn(evt.target);
    });
    cy.on("tap", "edge", function (evt) { describeEdge(evt.target); });
    cy.on("tap", function (evt) { if (evt.target === cy) clearFocus(); });
    cy.on("mouseover", "node", function (evt) { showTip(evt, nodeTip(evt.target)); });
    cy.on("mouseover", "edge", function (evt) { showTip(evt, edgeTip(evt.target)); });
    cy.on("mousemove", "node", function (evt) { placeTip(evt); });
    cy.on("mousemove", "edge", function (evt) { placeTip(evt); });
    cy.on("mouseout", "node", hideTip);
    cy.on("mouseout", "edge", hideTip);
    cy.on("pan zoom drag", hideTip);
    document.getElementById("graph").addEventListener("mouseleave", hideTip);

    // A big account renders as an unreadable hairball at (all projects), so
    // start scoped to the largest project rather than showing that first.
    if (GRAPH.stats.nodes > 400 && groups.length) {
      const size = {};
      for (const n of GRAPH.nodes) {
        const g = groupOf(n.id);
        if (g) size[g] = (size[g] || 0) + 1;
      }
      scopeSel.value = groups.slice().sort((a, b) => size[b] - size[a])[0];
    }
    applyGraphFilters();
    runLayout();
  }

  function showView(which) {
    const graphMode = which === "graph";
    const diagramMode = which === "diagram";
    const diagramWrap = document.getElementById("diagramWrap");
    wrap.style.display = graphMode ? "" : "none";
    if (diagramWrap) diagramWrap.style.display = diagramMode ? "" : "none";
    tableWrap.style.display = (graphMode || diagramMode) ? "none" : "";
    // The line legend describes the Cytoscape styling specifically - Mermaid
    // draws its own arrows and does not honour any of it.
    if (legend) legend.style.display = graphMode ? "flex" : "none";
    document.querySelectorAll("#viewSwitch button").forEach(b =>
      b.classList.toggle("on", b.dataset.view === which));
    if (diagramMode) { initDiagram(); return; }
    if (!graphMode) return;
    const firstInit = !cy;
    initGraph();
    // Cytoscape cannot measure a hidden container, so fit only once visible, and only the
    // first time: later visits keep the viewer's pan and zoom.
    if (cy && firstInit) { cy.resize(); cy.fit(cy.elements().filter(e => !e.hasClass("hidden")), 40); }
    else if (cy) { cy.resize(); }
  }
  document.querySelectorAll("#viewSwitch button").forEach(b =>
    b.addEventListener("click", () => showView(b.dataset.view)));

  ["graphHideIsolated", "graphHideHubs", "graphHideDeployment", "graphShowReportOnly", "graphShowAttr"]
    .forEach(id => document.getElementById(id).addEventListener("change", () => {
      applyGraphFilters(); runLayout();
    }));
  scopeSel.addEventListener("change", () => { applyGraphFilters(); runLayout(); });

  document.getElementById("graphLayout").addEventListener("change", () => {
    applyGraphFilters();
    runLayout();
  });

  // Fullscreen is a CSS overlay (works from file://); Cytoscape must be told it resized.
  const fullscreenBtn = document.getElementById("graphFullscreenBtn");
  function setFullscreen(on) {
    wrap.classList.toggle("fullscreen", on);
    document.body.style.overflow = on ? "hidden" : "";
    fullscreenBtn.textContent = on ? "Exit fullscreen" : "Fullscreen";
    if (cy) { cy.resize(); cy.fit(cy.elements().filter(e => !e.hasClass("hidden")), 40); }
  }
  fullscreenBtn.addEventListener("click", () => setFullscreen(!wrap.classList.contains("fullscreen")));
  document.addEventListener("keydown", (evt) => {
    if (evt.key === "Escape" && wrap.classList.contains("fullscreen")) setFullscreen(false);
  });

  // Keep the canvas in step with window resizes.
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    if (!cy || wrap.style.display === "none") return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => cy.resize(), 150);
  });
})();
"""
