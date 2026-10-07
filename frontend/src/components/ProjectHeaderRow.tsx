// One project banner inside the resource table: collapse toggle, name, row
// count and the rename control. "(no project)" has no project and no pencil.

import { InlineNameInput } from "./InlineNameInput";
import type { Project } from "../api/types";

interface Props {
  name: string;
  count: number;
  colSpan: number;
  collapsed: boolean;
  onToggle: () => void;
  /** Absent for "(no project)". */
  project: Project | undefined;
  editing: boolean;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onCommit: (name: string) => void;
  busy: boolean;
  error: string | null;
}

export function ProjectHeaderRow({
  name, count, colSpan, collapsed, onToggle,
  project, editing, onStartEdit, onCancelEdit, onCommit, busy, error,
}: Props) {
  const canEdit = Boolean(project && (project.group_id || project.seed_key));
  return (
    <tr className="group-header">
      <th colSpan={colSpan}>
        {editing ? (
          <InlineNameInput initial={name} onCommit={onCommit} onCancel={onCancelEdit} />
        ) : (
          <button className="linkish" onClick={onToggle}>
            {collapsed ? "▸" : "▾"} {name}
          </button>
        )}

        {!editing && canEdit && (
          <button
            className="linkish rename"
            onClick={onStartEdit}
            disabled={busy}
            title={project?.name_kind === "assigned"
              ? `Rename "${name}"`
              : `Assign a name - "${name}" is the default name`}
          >
            ✎
          </button>
        )}

        <span className="group-count">{count}</span>
        {error && <span className="banner error inline-error">{error}</span>}
      </th>
    </tr>
  );
}
