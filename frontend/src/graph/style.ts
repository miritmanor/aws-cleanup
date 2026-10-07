// The dependency graph's Cytoscape stylesheet. Line styles carry meaning; see
// the legend at the top of GraphPanel.tsx.

import type cytoscape from "cytoscape";

export const STYLE: cytoscape.StylesheetJson = [
  // Wrapped, not ellipsised: shortLabel already decided what fits.
  { selector: "node", style: {
      "background-color": "data(color)", label: "data(label)", "font-size": "9px",
      color: "#3d4a57", "text-valign": "bottom", "text-margin-y": 3,
      width: 18, height: 18, "text-max-width": "108px", "text-wrap": "wrap",
      "border-width": 1, "border-color": "rgba(0,0,0,0.25)" } },
  { selector: 'node[risk ^= "HIGH"]', style: {
      "border-width": 3, "border-color": "#c0392b" } },
  { selector: 'node[kind = "stub"]', style: {
      "background-opacity": 0.12, "background-color": "#9aa7b4",
      "border-style": "dashed", "border-color": "#9aa7b4", color: "#7b8794" } },
  { selector: "node[?isGroup]", style: {
      "background-opacity": 0.06, "background-color": "#8fa3b8",
      "border-width": 1.5, "border-style": "dashed", "border-color": "#8fa3b8",
      label: "data(label)", "font-size": "11px", "font-weight": "bold",
      color: "#5b6b7c", "text-valign": "top", "text-halign": "center",
      padding: "14px", shape: "round-rectangle" } },
  // After the group rule so it wins; quieter than a project box so it never reads as one.
  { selector: "node[?isTier]", style: {
      "background-opacity": 0.05, "border-width": 1, "border-style": "dotted",
      "font-size": "9.5px", "font-weight": "normal", padding: "10px" } },
  { selector: 'node[?isTier][label = "deployment"]', style: {
      "background-color": "#8b7ab8", "border-color": "#8b7ab8", color: "#6b4fa8" } },
  { selector: 'node[?isTier][label = "runtime"]', style: {
      "background-color": "#5f9e77", "border-color": "#5f9e77", color: "#1c6b39" } },
  { selector: "edge", style: {
      width: 1.4, "line-color": "#5b6b7c", "target-arrow-color": "#5b6b7c",
      "target-arrow-shape": "triangle", "arrow-scale": 0.7,
      // Bezier so the two arcs of a bidirectional pair stay separately
      // visible - direction is real information here, not decoration.
      "curve-style": "bezier", opacity: 0.75 } },
  { selector: "edge[!grouping]", style: {
      "line-style": "dashed", "line-color": "#b07d2b",
      "target-arrow-color": "#b07d2b" } },
  { selector: 'edge[category = "attribute"]', style: {
      "line-style": "dotted", "line-color": "#9aa7b4", width: 1,
      "target-arrow-shape": "none", opacity: 0.5 } },
  { selector: 'edge[state = "dangling"]', style: {
      "line-style": "dotted", "line-color": "#9aa7b4",
      "target-arrow-color": "#9aa7b4", opacity: 0.55 } },
  // Last, so it wins: a bridged link must not look like one AWS states directly.
  { selector: "edge[?bridged]", style: {
      "line-style": "solid", "line-color": "#7b93b8", width: 1,
      "target-arrow-color": "#7b93b8", opacity: 0.6 } },
  { selector: ".hidden", style: { display: "none" } },
  { selector: "node.picked", style: { "border-width": 3, "border-color": "#1668c4" } },
  { selector: "edge.picked", style: { width: 3, "line-color": "#1668c4",
      "target-arrow-color": "#1668c4", opacity: 1 } },
];
