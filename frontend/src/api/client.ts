// Every call to the backend, in one place. Server error messages ({"detail": ...})
// are shown verbatim: they name the problem and say what to do.

import type {
  AgentQueryRequest,
  AgentStatus,
  BillingSummary,
  ConfigResponse,
  ConfigUpdate,
  DefaultRegions,
  DocumentUploadResult,
  Graph,
  Group,
  Project,
  IconPack,
  MermaidResponse,
  Overview,
  ProfilesResponse,
  ScanRequest,
  ScanStatus,
  ScanSummary,
  ServiceMeta,
  UploadedDocument,
} from "./types";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.name = "ApiError";
  }

  /** A scan was requested while one was running. The UI disables the button,
   *  so this is the backstop rather than the expected path. */
  get isScanRunning(): boolean {
    return this.status === 409;
  }

  /** No scan yet: an empty state (the first run), not a failure. */
  get isMissingScan(): boolean {
    return this.status === 404;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      // FormData sets its own Content-Type (with the multipart boundary); never override it.
      ...(init?.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    throw new ApiError(response.status, await readDetail(response));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/** The server's own message, or the best description available. Pydantic 422
 *  arrays are flattened into readable "field: message" text. */
async function readDetail(response: Response): Promise<string> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return `${response.status} ${response.statusText}`;
  }

  const detail = (body as { detail?: unknown })?.detail;
  if (typeof detail === "string") return detail;

  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const entry = item as { loc?: unknown[]; msg?: string };
        const where = (entry.loc ?? []).filter((p) => p !== "body").join(".");
        return where ? `${where}: ${entry.msg}` : String(entry.msg);
      })
      .join("; ");
  }
  return `${response.status} ${response.statusText}`;
}

/** POST /api/agent/query, read as a stream of SSE tokens (EventSource is GET-only,
 *  so fetch's streaming body is used). */
async function* streamAgentQuery(body: AgentQueryRequest): AsyncGenerator<string> {
  const response = await fetch("/api/agent/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) {
    throw new ApiError(response.status, await readDetail(response));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // Frames are blank-line-delimited and may split across chunks: keep the remainder.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.trim();
      if (!line.startsWith("data:")) continue;
      let payload: { token?: string; error?: string };
      try {
        payload = JSON.parse(line.slice("data:".length).trim());
      } catch {
        // A malformed frame is dropped rather than aborting the whole reply.
        continue;
      }
      // A failed turn arrives as a final {"error"} frame (the 200 is already sent).
      if (payload.error) throw new ApiError(response.status, payload.error);
      if (payload.token) yield payload.token;
    }
  }
}

export const api = {
  getScan: () => request<ScanSummary>("/api/scan"),
  getGraph: (allNodes?: boolean) =>
    request<Graph>(
      allNodes === undefined ? "/api/graph" : `/api/graph?all_nodes=${allNodes}`,
    ),
  getServiceMeta: () => request<ServiceMeta>("/api/service-meta"),

  /** The bill and which of it the scan can speak for; read from the snapshot. */
  getBilling: () => request<BillingSummary>("/api/billing"),

  // The diagram's AWS icons ("aws:lambda"), and its Mermaid source from render_mermaid()
  // (projectId: one project plus one hop; omitted means the whole account).
  getArchitectureIcons: () => request<IconPack>("/api/architecture-icons"),
  getMermaid: (projectId?: string | null) =>
    request<MermaidResponse>(
      projectId
        ? `/api/mermaid?project_id=${encodeURIComponent(projectId)}`
        : "/api/mermaid",
    ),

  // The overview: summaries and circle positions, computed in Python like the CLI report's.
  getOverview: () => request<Overview>("/api/overview"),

  startScan: (body: ScanRequest) =>
    request<ScanStatus>("/api/scans", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // A request, not a kill: the scan ends before its next AWS step and saves
  // nothing. Polling the status is how the UI learns it has ended.
  cancelScan: () => request<ScanStatus>("/api/scans/cancel", { method: "POST" }),
  getScanStatus: () => request<ScanStatus>("/api/scans/status"),
  // Read from the file the last scan wrote; no AWS call.
  getDefaultRegions: () => request<DefaultRegions>("/api/scans/default-regions"),
  // Section names in the credentials file, parsed by the same Python that
  // lists them in a failed scan's error message.
  getProfiles: () => request<ProfilesResponse>("/api/profiles"),

  getProjects: () => request<Project[]>("/api/projects"),
  getGroups: () => request<Group[]>("/api/groups"),
  createGroup: (name: string, memberKey: string) =>
    request<Group>("/api/groups", {
      method: "POST",
      body: JSON.stringify({ name, member_key: memberKey }),
    }),
  // Hand-picked membership, regardless of what was detected.
  // createGroup above claims the whole project around ONE seed instead.
  createManualGroup: (name: string, memberKeys: string[]) =>
    request<Group>("/api/groups/manual", {
      method: "POST",
      body: JSON.stringify({ name, member_keys: memberKeys }),
    }),
  addGroupMembers: (id: string, memberKeys: string[]) =>
    request<Group>(`/api/groups/${encodeURIComponent(id)}/members`, {
      method: "POST",
      body: JSON.stringify({ member_keys: memberKeys }),
    }),
  renameGroup: (id: string, name: string) =>
    request<Group>(`/api/groups/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify({ name }),
    }),
  deleteGroup: (id: string) =>
    request<void>(`/api/groups/${encodeURIComponent(id)}`, { method: "DELETE" }),

  getConfig: () => request<ConfigResponse>("/api/config"),
  putConfig: (body: ConfigUpdate) =>
    request<ConfigResponse>("/api/config", {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  // The agent's corpus. Uploads are not indexed until reindexDocuments() is called,
  // which AgentDocuments does after every upload and delete.
  getDocuments: () => request<UploadedDocument[]>("/api/agent/documents"),
  uploadDocuments: (files: File[]) => {
    const body = new FormData();
    for (const file of files) body.append("files", file);
    return request<DocumentUploadResult>("/api/agent/documents", {
      method: "POST",
      body,
    });
  },
  deleteDocument: (name: string) =>
    request<void>(`/api/agent/documents/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
  // Slow by nature — it re-embeds the whole corpus and returns the listing
  // with the chunk counts it just produced.
  reindexDocuments: () =>
    request<UploadedDocument[]>("/api/agent/documents/reindex", {
      method: "POST",
    }),

  // Asked once on mount. Never throws for a missing key: that is `available: false`.
  getAgentStatus: () => request<AgentStatus>("/api/agent/status"),
  queryAgent: (body: AgentQueryRequest) => streamAgentQuery(body),
};
