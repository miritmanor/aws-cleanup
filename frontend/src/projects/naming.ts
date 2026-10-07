// Naming a project, from the list Python serves (GET /api/projects).
// Every project can be named; a first name seeds a saved name with one member.

import { api } from "../api/client";
import type { Project } from "../api/types";

/** Projects by project_id - what table banners and graph boxes are keyed on. */
export function projectsById(projects: Project[]): Map<string, Project> {
  return new Map(projects.map((p) => [p.project_id, p]));
}

/** Rename a project: PUT once it has an assigned name, else a seeded POST. */
export function renameProject(project: Project, name: string): Promise<unknown> {
  if (project.group_id) return api.renameGroup(project.group_id, name);
  if (project.seed_key) return api.createGroup(name, project.seed_key);
  return Promise.reject(new Error(`"${project.name}" has no resources left to name.`));
}
