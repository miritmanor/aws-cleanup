"""Graph panel layout: each project is laid out alone, then the extents are tiled, so
boxes cannot overlap. Format VALUES: braces NOT doubled, never .format() them."""

# No extra <script>: the layout is arithmetic plus Cytoscape's built-in layouts.
LAYOUT_HEAD = ""

# "Clusters" first because it is the default; options say what each is for.
LAYOUT_CONTROL = """
      <div>Layout <select id="graphLayout">
        <option value="clusters">Clusters - shape of each project</option>
        <option value="layered">Layered - dependency order, top to bottom</option>
        <option value="compact">Compact - fastest, for large graphs</option>
      </select></div>
"""

LAYOUT_JS = r"""
// --- Graph layout: top-level functions called by runLayout() in the graph IIFE (which is
// concatenated after this). Each project is laid out alone, then the boxes are tiled.

// Past this many VISIBLE nodes, use the instant layout; scoping to one project restores
// the good one.
const LAYOUT_FORCE_LIMIT = 800;

// Gaps between boxes; the project gap leaves room for its title.
const LAYOUT_PROJECT_GAP = 90;
const LAYOUT_TIER_GAP = 55;

const LAYOUT_INNER = {
  // nodeOverlap is cose's only overlap lever (a repulsion multiplier), raised well past
  // the default of 4.
  clusters: {name: "cose", randomize: false, nodeDimensionsIncludeLabels: true,
             componentSpacing: 90, nodeOverlap: 60},
  // breadthfirst is built in and directed; packing keeps the boxes apart.
  layered: {name: "breadthfirst", directed: true, spacingFactor: 1.2,
            avoidOverlap: true, nodeDimensionsIncludeLabels: true},
  // Packed like the others: "fastest" is still non-overlapping.
  compact: {name: "concentric", nodeDimensionsIncludeLabels: true,
            concentric: function (n) { return n.degree(); },
            levelWidth: function () { return 2; }},
};

// The outermost compound ancestor: the project box, not a tier compartment inside it.
function topGroupOf(node) {
  let cur = node, parent = node.parent();
  while (parent.length) { cur = parent; parent = parent.parent(); }
  return cur === node ? null : cur;
}

// A layout that throws must not blank the panel.
function runLayoutSafely(eles, options) {
  try {
    eles.layout(Object.assign({}, options, {animate: false, fit: false})).run();
  } catch (err) {
    try {
      eles.layout({name: "grid", animate: false, fit: false}).run();
    } catch (err2) { /* nothing left to try; positions stay as they are */ }
  }
}

// One bucket of leaf nodes laid out alone; outside edges are excluded so it can be tiled.
function layoutBucket(nodes, inner) {
  if (nodes.length < 2) return;
  runLayoutSafely(nodes.union(nodes.edgesWith(nodes)), inner);
}

function shiftNodes(nodes, dx, dy) {
  nodes.positions(function (n) {
    const p = n.position();
    return {x: p.x + dx, y: p.y + dy};
  });
}

// Tiles finished boxes into a wrapped grid, largest first, measured from the compound
// parent where there is one (its title and padding count).
function tileBoxes(items, gap) {
  if (items.length < 2) return;
  items.sort(function (a, b) { return b.box.w * b.box.h - a.box.w * a.box.h; });
  const total = items.reduce(function (sum, i) { return sum + i.box.w * i.box.h; }, 0);
  // Roughly square overall, but never narrower than the widest single box or
  // that box would wrap onto a line of its own every time.
  const budget = Math.max(Math.sqrt(total) * 1.6, items[0].box.w);

  let x = 0, y = 0, rowHeight = 0;
  items.forEach(function (item) {
    if (x > 0 && x + item.box.w > budget) { x = 0; y += rowHeight + gap; rowHeight = 0; }
    shiftNodes(item.nodes, x - item.box.x1, y - item.box.y1);
    x += item.box.w + gap;
    rowHeight = Math.max(rowHeight, item.box.h);
  });
}

function measure(nodes, parent) {
  return (parent && parent.length ? parent : nodes).boundingBox({includeLabels: true});
}

// One project: its tier compartments packed inside it, or a single layout when
// it has none. Returns nothing; the caller measures the result.
function layoutProject(leaves, inner) {
  const byCompartment = new Map();
  leaves.forEach(function (n) {
    const parent = n.parent();
    const key = parent.length && parent.data("isTier") ? parent.id() : "";
    if (!byCompartment.has(key)) byCompartment.set(key, n.cy().collection());
    byCompartment.set(key, byCompartment.get(key).union(n));
  });

  if (byCompartment.size < 2) {
    layoutBucket(leaves, inner);
    return;
  }
  const items = [];
  byCompartment.forEach(function (nodes, key) {
    layoutBucket(nodes, inner);
    const box = key ? nodes.cy().getElementById(key) : null;
    items.push({nodes: nodes, box: measure(nodes, box)});
  });
  tileBoxes(items, LAYOUT_TIER_GAP);
}

// choice is the select's value. Returns nothing - it fits the viewport itself.
function runGraphLayout(cy, choice) {
  const visible = cy.elements().filter(function (e) { return !e.hasClass("hidden"); });
  const leaves = visible.nodes().filter(function (n) { return n.isChildless(); });
  if (!leaves.length) return;

  if (leaves.length > LAYOUT_FORCE_LIMIT) {
    // The one unpacked path: too many nodes to read as boxes anyway.
    runLayoutSafely(visible, {name: "concentric", padding: 40,
                              nodeDimensionsIncludeLabels: true,
                              concentric: function (n) { return n.degree(); },
                              levelWidth: function () { return 2; }});
    cy.fit(visible, 40);
    return;
  }

  const inner = LAYOUT_INNER[choice] || LAYOUT_INNER.clusters;
  const byProject = new Map();
  leaves.forEach(function (n) {
    const group = topGroupOf(n);
    // Ungrouped rows share one bucket so they tile as a block rather than each
    // becoming its own box - they are not a project and must not draw as one.
    const key = group ? group.id() : "";
    if (!byProject.has(key)) byProject.set(key, cy.collection());
    byProject.set(key, byProject.get(key).union(n));
  });

  const items = [];
  byProject.forEach(function (nodes, key) {
    layoutProject(nodes, inner);
    items.push({nodes: nodes, box: measure(nodes, key ? cy.getElementById(key) : null)});
  });
  tileBoxes(items, LAYOUT_PROJECT_GAP);
  cy.fit(visible, 40);
}
"""
