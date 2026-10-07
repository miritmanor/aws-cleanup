// The resource table: a plain <table>, sorted, filtered and grouped by hand, every row
// rendered. COLUMNS (resourceColumns.tsx) is the only place row field names are spelled.

import { useCallback, useMemo, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import { ApiError, api } from "../api/client";
import { rowKey, text } from "../rows";
import { projectsById, renameProject } from "../projects/naming";
import { ProjectHeaderRow } from "./ProjectHeaderRow";
import type { Group, Project, Row, ServiceMeta } from "../api/types";

import { CHECKBOX_COL_WIDTH, COLUMNS, stickyAttrs } from "./resourceColumns";
import { ResourceRow, buildConnectionLinkPattern } from "./ResourceRow";

// Banding key for rows with no project; never a project_id.
const NO_PROJECT = "";
const NO_PROJECT_NAME = "(no project)";

type SortDir = "asc" | "desc";

interface Props {
  rows: Row[];
  serviceMeta: ServiceMeta;
  /** Set by clicking a graph node; filters to one resource and its group. */
  focusKey: string | null;
  /** Jump to one row - also wired to the connections column, see renderConnections. */
  onFocusKey: (key: string) => void;
  onClearFocus: () => void;
  /** Saved names, for the "add selection to an existing project" control. */
  namedGroups: Group[];
  /** GET /api/projects: banner names and rename targets, keyed by project_id. */
  projects: Project[];
  /** A manual-grouping action changed the group store - reload rows and groups. */
  onGroupsChanged: () => void;
  /** Checkbox multi-select (lifted to App.tsx, also scopes the agent), keyed by rowKey(). */
  selected: Set<string>;
  setSelected: Dispatch<SetStateAction<Set<string>>>;
}

/** The flag filter: staleness.usage_state's three values, named by flag word. */
const USAGE_OPTIONS: [string, string][] = [
  ["unused", "STALE"],
  ["active", "ACTIVE"],
  ["unknown", "UNKNOWN"],
];

export function ResourceTable({
  rows,
  serviceMeta,
  focusKey,
  onFocusKey,
  onClearFocus,
  namedGroups,
  projects,
  onGroupsChanged,
  selected,
  setSelected,
}: Props) {
  const [query, setQuery] = useState("");
  const [service, setService] = useState("");
  const [region, setRegion] = useState("");
  const [flag, setFlag] = useState("");
  const [sortKey, setSortKey] = useState("service");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [grouped, setGrouped] = useState(true);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [newGroupName, setNewGroupName] = useState("");
  const [addToGid, setAddToGid] = useState("");
  const [groupError, setGroupError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Renaming a project from its banner: the same act as on the Projects tab.
  const [editingGroup, setEditingGroup] = useState<string | null>(null);
  const [renameError, setRenameError] = useState<string | null>(null);

  const projectIndex = useMemo(() => projectsById(projects), [projects]);

  const services = useMemo(
    () => [...new Set(rows.map((r) => text(r.service)))].sort(),
    [rows],
  );
  const regions = useMemo(
    () => [...new Set(rows.map((r) => text(r.region)))].sort(),
    [rows],
  );
  // The AWS service (EC2, S3...) each type is filed under; Python decides it.
  const serviceOf = useCallback(
    (row: Row) => serviceMeta[text(row.service)]?.service ?? text(row.service),
    [serviceMeta],
  );
  const awsServices = useMemo(
    () => [...new Set(rows.map(serviceOf))].sort(),
    [rows, serviceOf],
  );

  // Connection sentences name targets by service+id with no region, so links resolve
  // over the whole scan and only when that pair is unique.
  const connectionLinkIndex = useMemo(() => {
    const index = new Map<string, Row[]>();
    for (const row of rows) {
      const key = `${text(row.service)}:${text(row.resource_id)}`;
      const bucket = index.get(key);
      if (bucket) bucket.push(row);
      else index.set(key, [row]);
    }
    return index;
  }, [rows]);

  const connectionLinkPattern = useMemo(
    () => buildConnectionLinkPattern(services),
    [services],
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return rows.filter((row) => {
      if (service && serviceOf(row) !== service) return false;
      if (region && text(row.region) !== region) return false;
      if (flag && text(row.usage_state) !== flag) return false;
      if (focusKey && rowKey(row) !== focusKey) return false;
      if (!needle) return true;
  // Free text searches every shown column, e.g. a resource id or ARN fragment.
      return COLUMNS.some((c) => text(row[c.key]).toLowerCase().includes(needle));
    });
  }, [rows, serviceOf, query, service, region, flag, focusKey]);

  const sorted = useMemo(() => {
    const column = COLUMNS.find((c) => c.key === sortKey);
    const factor = sortDir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const x = a[sortKey];
      const y = b[sortKey];
      // Blanks sort last in both directions. A column half full of empty
      // strings otherwise puts them all at one end and buries the data.
      const xEmpty = x == null || x === "";
      const yEmpty = y == null || y === "";
      if (xEmpty && yEmpty) return 0;
      if (xEmpty) return 1;
      if (yEmpty) return -1;
      if (column?.numeric) return (Number(x) - Number(y)) * factor;
      return text(x).localeCompare(text(y)) * factor;
    });
  }, [filtered, sortKey, sortDir]);

  const groups = useMemo(() => {
    if (!grouped) return null;
    const out = new Map<string, Row[]>();
    for (const row of sorted) {
      const key = text(row.project_id) || NO_PROJECT;
      const bucket = out.get(key);
      if (bucket) bucket.push(row);
      else out.set(key, [row]);
    }
    // Largest first, with resources in no project always last.
    return [...out.entries()].sort((a, b) => {
      if (a[0] === NO_PROJECT) return 1;
      if (b[0] === NO_PROJECT) return -1;
      return b[1].length - a[1].length || a[0].localeCompare(b[0]);
    });
  }, [sorted, grouped]);

  function toggleSort(key: string) {
    if (key === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  function toggleGroup(name: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  const allVisibleSelected = sorted.length > 0 && sorted.every((r) => selected.has(rowKey(r)));

  function toggleSelected(key: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function toggleSelectAllVisible() {
    setSelected((prev) => {
      if (allVisibleSelected) {
        const next = new Set(prev);
        for (const r of sorted) next.delete(rowKey(r));
        return next;
      }
      const next = new Set(prev);
      for (const r of sorted) next.add(rowKey(r));
      return next;
    });
  }

  function clearSelection() {
    setSelected(new Set());
    setGroupError(null);
    setNewGroupName("");
    setAddToGid("");
  }

  async function createGroupFromSelection() {
    const name = newGroupName.trim();
    if (!name || selected.size < 2) return;
    setGroupError(null);
    setBusy(true);
    try {
      await api.createManualGroup(name, [...selected]);
      clearSelection();
      onGroupsChanged();
    } catch (e) {
      setGroupError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function commitProjectName(project: Project, name: string) {
    setRenameError(null);
    setBusy(true);
    try {
      await renameProject(project, name);
      setEditingGroup(null);
      onGroupsChanged();
    } catch (e) {
      // The edit stays open on failure - the message names what to fix
      // (for example, a member already in another saved project).
      setRenameError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function addSelectionToGroup() {
    if (!addToGid || selected.size === 0) return;
    setGroupError(null);
    setBusy(true);
    try {
      await api.addGroupMembers(addToGid, [...selected]);
      clearSelection();
      onGroupsChanged();
    } catch (e) {
      setGroupError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="table-panel">
      <div className="filters">
        <input
          type="search"
          placeholder="Search every column…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="search"
        />
        <select value={service} onChange={(e) => setService(e.target.value)}>
          <option value="">All services</option>
          {awsServices.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select value={region} onChange={(e) => setRegion(e.target.value)}>
          <option value="">All regions</option>
          {regions.map((r) => (
            <option key={r} value={r}>{r}</option>
          ))}
        </select>
        <select value={flag} onChange={(e) => setFlag(e.target.value)}>
          <option value="">Any flag</option>
          {USAGE_OPTIONS.map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
        <label className="inline">
          <input
            type="checkbox"
            checked={grouped}
            onChange={(e) => setGrouped(e.target.checked)}
          />
          Group by project
        </label>
        <span className="count">
          {sorted.length} of {rows.length}
        </span>
        {focusKey && (
          <button className="linkish" onClick={onClearFocus}>
            show all rows
          </button>
        )}
        {/* Rendered by present/csv.py, not built from `rows` here: the column
            list and display_row() are one decision and it is Python's. */}
        <a className="linkish download" href="/api/export/inventory.csv" download>
          Download CSV
        </a>
      </div>

      {selected.size > 0 && (
        <div className="selection-bar">
          <span className="count">{selected.size} selected</span>
          <button className="linkish" onClick={clearSelection} disabled={busy}>
            clear selection
          </button>
          <span className="divider" />
          <input
            type="text"
            placeholder="New project name…"
            value={newGroupName}
            onChange={(e) => setNewGroupName(e.target.value)}
            disabled={busy}
          />
          <button
            onClick={() => void createGroupFromSelection()}
            disabled={busy || selected.size < 2 || !newGroupName.trim()}
            title={selected.size < 2 ? "Select at least two resources to put them in a project" : undefined}
          >
            Create project
          </button>
          {namedGroups.length > 0 && (
            <>
              <span className="divider" />
              <select value={addToGid} onChange={(e) => setAddToGid(e.target.value)} disabled={busy}>
                <option value="">Add to existing project…</option>
                {namedGroups.map((g) => (
                  <option key={g.id} value={g.id}>{g.name}</option>
                ))}
              </select>
              <button onClick={() => void addSelectionToGroup()} disabled={busy || !addToGid}>
                Add
              </button>
            </>
          )}
          {groupError && <span className="banner error inline-error">{groupError}</span>}
        </div>
      )}

      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th
                className="sticky checkbox-col"
                style={{ left: 0, width: CHECKBOX_COL_WIDTH, minWidth: CHECKBOX_COL_WIDTH }}
              >
                <input
                  type="checkbox"
                  checked={allVisibleSelected}
                  onChange={toggleSelectAllVisible}
                  title="Select all visible rows"
                />
              </th>
              {COLUMNS.map((c) => {
                const sticky = stickyAttrs(c);
                return (
                  <th
                    key={c.key}
                    onClick={() => toggleSort(c.key)}
                    className={`${c.numeric ? "numeric" : ""} ${c.wide ? "wide" : ""} ${sticky.className}`}
                    style={sticky.style}
                    title="Sort"
                  >
                    {c.label}
                    <span className="sort-arrow">
                      {sortKey === c.key ? (sortDir === "asc" ? "▲" : "▼") : ""}
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>

          {groups ? (
            groups.map(([pid, groupRows]) => {
              const project = projectIndex.get(pid);
              const name = pid === NO_PROJECT ? NO_PROJECT_NAME
                : project?.name || text(groupRows[0].project_group) || pid;
              return (
              <tbody key={pid}>
                <ProjectHeaderRow
                  name={name}
                  count={groupRows.length}
                  colSpan={COLUMNS.length + 1}
                  collapsed={collapsed.has(pid)}
                  onToggle={() => toggleGroup(pid)}
                  project={project}
                  editing={editingGroup === pid}
                  onStartEdit={() => { setRenameError(null); setEditingGroup(pid); }}
                  onCancelEdit={() => { setRenameError(null); setEditingGroup(null); }}
                  onCommit={(next) => { if (project) void commitProjectName(project, next); }}
                  busy={busy}
                  error={editingGroup === pid ? renameError : null}
                />
                {!collapsed.has(pid) &&
                  groupRows.map((row) => (
                    <ResourceRow
                      key={rowKey(row)}
                      row={row}
                      meta={serviceMeta}
                      selected={selected.has(rowKey(row))}
                      onToggleSelect={() => toggleSelected(rowKey(row))}
                      connectionLinkIndex={connectionLinkIndex}
                      connectionLinkPattern={connectionLinkPattern}
                      onFocusKey={onFocusKey}
                    />
                  ))}
              </tbody>
              );
            })
          ) : (
            <tbody>
              {sorted.map((row) => (
                <ResourceRow
                  key={rowKey(row)}
                  row={row}
                  meta={serviceMeta}
                  selected={selected.has(rowKey(row))}
                  onToggleSelect={() => toggleSelected(rowKey(row))}
                  connectionLinkIndex={connectionLinkIndex}
                  connectionLinkPattern={connectionLinkPattern}
                  onFocusKey={onFocusKey}
                />
              ))}
            </tbody>
          )}
        </table>
        {sorted.length === 0 && (
          <p className="empty">Nothing matches those filters.</p>
        )}
      </div>
    </section>
  );
}
