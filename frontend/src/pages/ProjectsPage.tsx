// Every project, with its name, source and saved-name state - served by Python
// (GET /api/projects). Click a name to assign or change it; no re-scan needed.

import { useState } from "react";
import { ApiError, api } from "../api/client";
import { InlineNameInput } from "../components/InlineNameInput";
import { renameProject } from "../projects/naming";
import type { Project } from "../api/types";

interface Props {
  projects: Project[];
  onChanged: () => void;
}

export function ProjectsPage({ projects, onChanged }: Props) {
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);

  async function act(work: () => Promise<unknown>) {
    setError(null);
    try {
      await work();
      onChanged();
      return true;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      return false;
    }
  }

  async function commitEdit(project: Project, name: string) {
    if (await act(() => renameProject(project, name))) setEditingId(null);
  }

  function mergeInto(source: Project, targetId: string) {
    const target = projects.find((p) => p.project_id === targetId);
    if (!target) return;
    if (confirm(`Merge "${source.name}" into "${target.name}"? Its resources will be `
      + `shown under "${target.name}". Forget the name "${target.name}" to split them apart again.`)) {
      void act(() => api.mergeProject(source.project_id, target.project_id));
    }
  }

  function forget(project: Project) {
    const split = project.merged_from.length
      ? ` ${project.merged_from.join(", ")} will be split back out.` : "";
    if (confirm(`Forget the name "${project.name}"? The project goes back to its default name.${split}`)) {
      void act(() => api.deleteGroup(project.group_id!));
    }
  }

  // A project with no resources left has nothing to merge or to be merged into.
  const mergeTargets = projects.filter((p) => p.resource_count > 0);

  return (
    <section className="page">
      <h2>Projects</h2>
      <p className="lede">
        Every project this scan found, largest first. Click a name to assign
        one or change it. An assigned name is saved against the resources in
        the project, so it survives the project gaining or losing a resource,
        and it takes effect immediately - no re-scan needed. Use "Merge into…"
        to show one project under another's name; forgetting that name splits
        them apart again.
      </p>

      {error && <div className="banner error">{error}</div>}

      <table className="projects">
        <thead>
          <tr>
            <th>Name</th>
            <th>Source</th>
            <th>Resources</th>
            <th>Updated</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {projects.map((project) => {
            const editing = editingId === project.project_id;
            const canEdit = Boolean(project.group_id || project.seed_key);
            return (
              <tr key={project.project_id}>
                <td>
                  {editing ? (
                    <InlineNameInput
                      initial={project.name}
                      onCommit={(name) => void commitEdit(project, name)}
                      onCancel={() => setEditingId(null)}
                    />
                  ) : canEdit ? (
                    <button
                      className="linkish name-edit"
                      onClick={() => { setError(null); setEditingId(project.project_id); }}
                    >
                      {project.name}
                    </button>
                  ) : (
                    <strong>{project.name}</strong>
                  )}
                  {!editing && project.name_kind === "default" && (
                    <div className="hint">default name</div>
                  )}
                  {!editing && project.manual && (
                    <div className="hint">hand-picked membership - does not grow on its own</div>
                  )}
                  {!editing && project.extra_members > 0 && (
                    <div className="hint">{project.extra_members} added by hand</div>
                  )}
                  {!editing && project.merged_from.length > 0 && (
                    <div className="hint">merged from: {project.merged_from.join(", ")}</div>
                  )}
                  {!editing && project.not_applied && (
                    <div className="hint warn">not applied: {project.not_applied}</div>
                  )}
                </td>
                <td className="muted">{project.sources.join(", ") || "—"}</td>
                <td>
                  {project.resource_count}
                  {project.group_id && project.live_members === 0 && (
                    <span className="warn"> none present</span>
                  )}
                </td>
                <td className="muted">{project.updated_at ?? "—"}</td>
                <td className="actions">
                  {project.resource_count > 0 && mergeTargets.length > 1 && (
                    <select
                      className="merge-into"
                      value=""
                      onChange={(e) => mergeInto(project, e.target.value)}
                    >
                      <option value="">Merge into…</option>
                      {mergeTargets
                        .filter((p) => p.project_id !== project.project_id)
                        .map((p) => (
                          <option key={p.project_id} value={p.project_id}>
                            {p.name} ({p.resource_count})
                          </option>
                        ))}
                    </select>
                  )}
                  {project.group_id && (
                    <button
                      className="linkish danger"
                      onClick={() => forget(project)}
                    >
                      forget
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
          {projects.length === 0 && (
            <tr>
              <td colSpan={5} className="empty">
                No projects - either there is no scan yet, or no tag, name rule
                or link put any resources together.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </section>
  );
}
