// The row, client side: the two helpers that read one. There is deliberately no Row
// interface; see api/types.ts.

import type { Row } from "./api/types";

export function text(value: unknown): string {
  return value == null ? "" : String(value);
}

/** The join key between table, graph and group store: "<service>:<region>:<resource_id>".
 *  Must match member_key() in rows.py and rowKey() in the HTML report. */
export function rowKey(row: Row): string {
  return `${text(row.service)}:${text(row.region)}:${text(row.resource_id)}`;
}
