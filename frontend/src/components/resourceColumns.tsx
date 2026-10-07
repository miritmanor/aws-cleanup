// The resource table's columns: the one place row field names are spelled (see
// ResourceTable.tsx), plus the pinned-column and pill helpers that read them.

import type { CSSProperties, ReactNode } from "react";
import { text } from "../rows";
import type { Row } from "../api/types";

interface Column {
  key: string;
  label: string;
  numeric?: boolean;
  wide?: boolean;
  /** Pixel width of a pinned column; also what makes it pinned at all. */
  sticky?: number;
  /** Marks the last pinned column, so it gets the divider shadow. */
  stickyEnd?: boolean;
}

export const COLUMNS: Column[] = [
  { key: "service", label: "Service", sticky: 130 },
  { key: "name", label: "Name", wide: true, sticky: 200, stickyEnd: true },
  { key: "resource_id", label: "Resource", wide: true },
  { key: "region", label: "Region" },
  { key: "flag", label: "Flag" },
  { key: "last_used", label: "Last used" },
  { key: "last_used_days", label: "Days", numeric: true },
  { key: "created", label: "Created" },
  // Charges over the last 30 days, not a monthly forecast; a blank "Cost basis" is not "free".
  { key: "est_monthly_cost_usd", label: "$ last 30d", numeric: true },
  { key: "cost", label: "Cost basis" },
  { key: "risk_if_removed", label: "Risk" },
  { key: "tier", label: "Runtime / deploy" },
  { key: "connections", label: "Connections", wide: true },
  { key: "why_grouped", label: "Why grouped", wide: true },
  { key: "notes", label: "Notes", wide: true },
  { key: "description", label: "Description", wide: true },
  { key: "tags", label: "Tags", wide: true },
];

// The select-for-grouping checkbox is pinned ahead of every data column, so
// the sticky offsets below start after its width rather than at the edge.
export const CHECKBOX_COL_WIDTH = 34;

// Left offset for each pinned column, computed once from the widths above -
// the second pinned column starts where the first one's width ends.
const STICKY_LEFT: Record<string, number> = {};
{
  let left = CHECKBOX_COL_WIDTH;
  for (const c of COLUMNS) {
    if (c.sticky) {
      STICKY_LEFT[c.key] = left;
      left += c.sticky;
    }
  }
}

export function stickyAttrs(c: Column): { className: string; style?: CSSProperties } {
  if (!c.sticky) return { className: "" };
  return {
    className: `sticky${c.stickyEnd ? " sticky-end" : ""}`,
    style: { left: STICKY_LEFT[c.key], width: c.sticky, minWidth: c.sticky, maxWidth: c.sticky },
  };
}

/** Pill class for vocabulary columns, mirroring riskClass() in present/html/template.py;
 *  an unknown value degrades to grey. */
export function pillClass(key: string, v: string): string {
  if (!v) return "";
  if (key === "risk_if_removed") {
    if (v.startsWith("HIGH")) return "risk-high";
    if (v.startsWith("MEDIUM")) return "risk-medium";
    if (v.startsWith("LOW")) return "risk-low";
    return "risk-unknown";
  }
  if (key === "flag") {
    if (v === "ERROR") return "flag-error";
    return v.startsWith("STALE") ? "flag-stale" : "";
  }
  if (key === "tier") {
    if (v === "runtime") return "tier-runtime";
    if (v === "deployment") return "tier-deployment";
  }
  return "";
}

/** Risk arrives as "HIGH - still referenced by ..." - the pill shows the
 *  verdict only, and the cell's title attribute keeps the reasoning. */
function pillText(key: string, v: string): string {
  return key === "risk_if_removed" ? v.split(" - ")[0] : v;
}

/** A pill, plus a flag's qualifier on its own wrapping line, so a long flag does not
 *  set the column's minimum width. */
export function renderPill(key: string, v: string, cls: string): ReactNode {
  const open = key === "flag" ? v.indexOf(" (") : -1;
  if (open === -1) return <span className={`pill ${cls}`}>{pillText(key, v)}</span>;
  return (
    <>
      <span className={`pill ${cls}`}>{v.slice(0, open)}</span>
      <span className="pill-qual">{v.slice(open + 1)}</span>
    </>
  );
}

/** A cell's hover text. The tier cell shows the rule that decided it, and
 *  nothing when the tier is only the default. */
export function cellTitle(key: string, value: string, row: Row): string | undefined {
  if (key === "tier") return text(row.why_tier) || undefined;
  return value;
}
