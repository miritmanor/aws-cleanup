// Positions for the dependency graph: a port of present/graph/layout.py, laid
// out per project and tiled so project boxes never overlap.

import cytoscape from "cytoscape";
import type { Core } from "cytoscape";

// The layouts offered, named for what they are FOR. "fcose" is not a thing
// anyone is looking for.
export const LAYOUTS = [
  { id: "clusters", label: "Clusters - shape of each project" },
  { id: "layered", label: "Layered - dependency order, top to bottom" },
  { id: "compact", label: "Compact - fastest, for large graphs" },
] as const;

export type LayoutChoice = (typeof LAYOUTS)[number]["id"];

// Past this many visible nodes any force layout costs more than it returns.
const LAYOUT_FORCE_LIMIT = 800;

// A port of present/graph/layout.py: each project is laid out alone and the extents
// tiled, so project boxes cannot overlap (cose and fcose did not manage that).

const LAYOUT_PROJECT_GAP = 90;
const LAYOUT_TIER_GAP = 55;

const LAYOUT_INNER: Record<LayoutChoice, Record<string, unknown>> = {
  clusters: { name: "cose", randomize: false, nodeDimensionsIncludeLabels: true,
              componentSpacing: 90, nodeOverlap: 12 },
  // breadthfirst rather than dagre: built in, directed, and the packing is what
  // keeps project boxes apart - so dagre would buy nothing.
  layered: { name: "breadthfirst", directed: true, spacingFactor: 1.2,
             avoidOverlap: true, nodeDimensionsIncludeLabels: true },
  // Packed like the others: "fastest" is still non-overlapping.
  compact: { name: "concentric", nodeDimensionsIncludeLabels: true,
             concentric: (n: cytoscape.NodeSingular) => n.degree(false),
             levelWidth: () => 2 },
};

/** The outermost compound ancestor: the project box, not the tier box in it. */
function topGroupOf(node: cytoscape.NodeSingular): cytoscape.NodeSingular | null {
  let current = node;
  let parent = node.parent();
  while (parent.length) {
    current = parent[0] as cytoscape.NodeSingular;
    parent = current.parent();
  }
  return current === node ? null : current;
}

// A layout that throws must not blank the panel.
// worse arrangement.
function runLayoutSafely(
  eles: cytoscape.CollectionReturnValue,
  options: Record<string, unknown>,
) {
  try {
    eles.layout({ ...options, animate: false, fit: false } as cytoscape.LayoutOptions).run();
  } catch {
    try {
      eles.layout({ name: "grid", animate: false, fit: false }).run();
    } catch { /* nothing left to try; positions stay as they are */ }
  }
}

// Edges leaving the bucket are excluded, so each project's arrangement is independent.
function layoutBucket(nodes: cytoscape.CollectionReturnValue, inner: Record<string, unknown>) {
  if (nodes.length < 2) return;
  runLayoutSafely(nodes.union(nodes.edgesWith(nodes)), inner);
}

function shiftNodes(nodes: cytoscape.CollectionReturnValue, dx: number, dy: number) {
  nodes.positions((n) => ({ x: n.position().x + dx, y: n.position().y + dy }));
}

interface Tile {
  nodes: cytoscape.CollectionReturnValue;
  box: cytoscape.BoundingBox12 & cytoscape.BoundingBoxWH;
}

// Tiles finished boxes into a wrapped grid, largest first, measured from the compound
// parent where there is one (its title and padding count).
function tileBoxes(items: Tile[], gap: number) {
  if (items.length < 2) return;
  items.sort((a, b) => b.box.w * b.box.h - a.box.w * a.box.h);
  const total = items.reduce((sum, i) => sum + i.box.w * i.box.h, 0);
  // Roughly square overall, but never narrower than the widest single box or
  // that box wraps onto a line of its own every time.
  const budget = Math.max(Math.sqrt(total) * 1.6, items[0].box.w);

  let x = 0, y = 0, rowHeight = 0;
  for (const item of items) {
    if (x > 0 && x + item.box.w > budget) { x = 0; y += rowHeight + gap; rowHeight = 0; }
    shiftNodes(item.nodes, x - item.box.x1, y - item.box.y1);
    x += item.box.w + gap;
    rowHeight = Math.max(rowHeight, item.box.h);
  }
}

function measure(
  nodes: cytoscape.CollectionReturnValue,
  parent: cytoscape.NodeSingular | cytoscape.CollectionReturnValue | null,
) {
  const target = parent && parent.length ? parent : nodes;
  return target.boundingBox({ includeLabels: true });
}

/** One project: its tier compartments packed inside it, or one layout when it
 * has none. */
function layoutProject(
  cy: Core,
  leaves: cytoscape.CollectionReturnValue,
  inner: Record<string, unknown>,
) {
  const byCompartment = new Map<string, cytoscape.CollectionReturnValue>();
  leaves.forEach((node) => {
    // parent() is a collection even for a single parent, so the node has to be
    // taken out of it before asking for an id.
    const parent = node.parent()[0] as cytoscape.NodeSingular | undefined;
    const key = parent && parent.data("isTier") ? parent.id() : "";
    byCompartment.set(key, (byCompartment.get(key) ?? cy.collection()).union(node));
  });

  if (byCompartment.size < 2) {
    layoutBucket(leaves, inner);
    return;
  }
  const items: Tile[] = [];
  byCompartment.forEach((nodes, key) => {
    layoutBucket(nodes, inner);
    items.push({ nodes, box: measure(nodes, key ? cy.getElementById(key) : null) });
  });
  tileBoxes(items, LAYOUT_TIER_GAP);
}

export function runLayout(cy: Core, choice: LayoutChoice) {
  const visible = cy.elements().filter((e) => !e.hasClass("hidden"));
  const leaves = visible.nodes().filter((n) => n.isChildless());
  if (!leaves.length) return;

  if (leaves.length > LAYOUT_FORCE_LIMIT) {
    // The one unpacked path: too many nodes to read as boxes anyway.
    runLayoutSafely(visible, {
      name: "concentric", padding: 40, nodeDimensionsIncludeLabels: true,
      concentric: (n: cytoscape.NodeSingular) => n.degree(false),
      levelWidth: () => 2,
    });
    cy.fit(visible, 40);
    return;
  }

  const inner = LAYOUT_INNER[choice] ?? LAYOUT_INNER.clusters;
  const byProject = new Map<string, cytoscape.CollectionReturnValue>();
  leaves.forEach((node) => {
    const group = topGroupOf(node);
    // Ungrouped rows share one bucket so they tile as a block rather than each
    // becoming its own box - they are not a project and must not draw as one.
    const key = group ? group.id() : "";
    byProject.set(key, (byProject.get(key) ?? cy.collection()).union(node));
  });

  const items: Tile[] = [];
  byProject.forEach((nodes, key) => {
    layoutProject(cy, nodes, inner);
    items.push({ nodes, box: measure(nodes, key ? cy.getElementById(key) : null) });
  });
  tileBoxes(items, LAYOUT_PROJECT_GAP);
  cy.fit(visible, 40);
}
