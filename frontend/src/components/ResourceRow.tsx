// One row of the resource table, and the connections cell that turns each
// "<service>:<id>" reference into a jump to that row.

import type { ReactNode } from "react";
import { rowKey, text } from "../rows";
import type { Row, ServiceMeta } from "../api/types";
import { CHECKBOX_COL_WIDTH, COLUMNS, cellTitle, pillClass, renderPill, stickyAttrs } from "./resourceColumns";

export function ResourceRow({
  row,
  meta,
  selected,
  onToggleSelect,
  connectionLinkIndex,
  connectionLinkPattern,
  onFocusKey,
}: {
  row: Row;
  meta: ServiceMeta;
  selected: boolean;
  onToggleSelect: () => void;
  connectionLinkIndex: Map<string, Row[]>;
  connectionLinkPattern: RegExp | null;
  onFocusKey: (key: string) => void;
}) {
  const service = text(row.service);
  const colour = meta[service]?.color ?? "#888";
  const flag = text(row.flag);
  return (
    <tr className={flag === "ERROR" ? "errored" : flag.startsWith("STALE") ? "stale" : ""}>
      <td
        className="sticky checkbox-col"
        style={{ left: 0, width: CHECKBOX_COL_WIDTH, minWidth: CHECKBOX_COL_WIDTH }}
      >
        <input type="checkbox" checked={selected} onChange={onToggleSelect} />
      </td>
      {COLUMNS.map((c) => {
        const sticky = stickyAttrs(c);
        if (c.key === "service") {
          return (
            <td key={c.key} className={`service-cell ${sticky.className}`} style={sticky.style}>
              <span className="dot" style={{ background: colour }} />
              {service}
            </td>
          );
        }
        if (c.key === "connections") {
          return (
            <td key={c.key} className={`wide ${sticky.className}`} style={sticky.style}>
              <div className="wide-inner">
                {renderConnections(
                  text(row[c.key]),
                  connectionLinkIndex,
                  connectionLinkPattern,
                  onFocusKey,
                )}
              </div>
            </td>
          );
        }
        if (c.key === "resource_id") {
          const url = text(row.console_url);
          return (
            <td key={c.key} className={`wide mono ${sticky.className}`} style={sticky.style}>
              <div className="wide-inner">
                {url ? (
                  <a href={url} target="_blank" rel="noreferrer">
                    {text(row.resource_id)}
                  </a>
                ) : (
                  text(row.resource_id)
                )}
              </div>
            </td>
          );
        }
        const value = text(row[c.key]);
        const pill = pillClass(c.key, value);
        return (
          <td
            key={c.key}
            className={`${c.numeric ? "numeric" : ""} ${c.wide ? "wide" : ""} ${sticky.className}`}
            style={sticky.style}
            title={c.wide ? undefined : cellTitle(c.key, value, row)}
          >
            {c.wide ? (
              <div className="wide-inner">{value}</div>
            ) : pill ? (
              renderPill(c.key, value, pill)
            ) : (
              value
            )}
          </td>
        );
      })}
    </tr>
  );
}

/** Matches "<service>:<id>" references in a connections sentence, anchored to this
 *  scan's service names (longest first) so ARNs never match. null without rows. */
export function buildConnectionLinkPattern(services: string[]): RegExp | null {
  const named = services.filter(Boolean);
  if (named.length === 0) return null;
  const escaped = [...named]
    .sort((a, b) => b.length - a.length)
    .map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return new RegExp(`\\b(${escaped.join("|")}):([^\\s[()]+)`, "g");
}

/** Turns each "<service>:<id>" reference that matches exactly one row into a jump to
 *  it; ambiguous and dangling ones stay plain text. */
function renderConnections(
  value: string,
  index: Map<string, Row[]>,
  pattern: RegExp | null,
  onFocusKey: (key: string) => void,
): ReactNode {
  if (!value || !pattern) return value;
  pattern.lastIndex = 0;
  const nodes: ReactNode[] = [];
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(value))) {
    const [full, service, id] = match;
    if (match.index > last) nodes.push(value.slice(last, match.index));
    const candidates = index.get(`${service}:${id}`);
    if (candidates && candidates.length === 1) {
      const key = rowKey(candidates[0]);
      nodes.push(
        <button
          key={match.index}
          type="button"
          className="conn-link"
          title={`Jump to ${full} in this table`}
          onClick={() => onFocusKey(key)}
        >
          {full}
        </button>,
      );
    } else {
      nodes.push(full);
    }
    last = match.index + full.length;
  }
  if (last < value.length) nodes.push(value.slice(last));
  return nodes;
}
