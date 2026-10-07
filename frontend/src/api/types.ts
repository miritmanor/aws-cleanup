// Hand-written mirrors of backend/app/schemas.py (`npm run gen:api` regenerates schema.d.ts).
// The row is deliberately not restated: its fields are Python's ROW_FIELDS.

export type ScanState = "idle" | "running" | "done" | "failed" | "cancelled";

export type Row = Record<string, string | number | boolean | null>;

/** One capability the scan tried, and how it went. Anything but "complete" means the
 *  rows are not the whole picture for that resource type. */
export interface CoverageEntry {
  account: string;
  scope: string;
  collector: string;
  service: string;
  capability: string;
  operation: string;
  status: "complete" | "partial" | "denied" | "throttled" | "failed" | "not_requested" | "unsupported";
  scanned_at: string;
  error: string;
  count: number;
}

/** How a resource's project was decided. "assigned" is the old "grouped";
 *  the other three are answers the previous model could not give. */
export type Membership = "assigned" | "shared" | "ambiguous" | "unassigned";

export interface ScanSummary {
  scanned_at: string | null;
  account: string;
  regions: string[];
  row_count: number;
  rows: Row[];
  coverage: CoverageEntry[];
}

/** One billed AWS service and how much of it the scan can speak for; the verdict
 *  is decided in Python. */
export interface BilledService {
  service: string;
  /** A decimal string, not a number: these are currency figures that have to
   *  reconcile against a total, and a float round trip loses that. */
  amount: string;
  state: "unsupported" | "blocked" | "not_found" | "partial" | "covered"
       | "not_a_resource";
  collected: string[];
  /** Resource types the same bill covers that this script does not collect.
   *  A statement about the script, never evidence such a resource exists. */
  gaps: string[];
  row_count: number;
  /** The finding, already worded by present/cost.py; rendered as given. */
  lines: string[];
}

export interface BillingSummary {
  queried: boolean;
  complete: boolean;
  estimated: boolean;
  error: string;
  currency: string;
  period_start: string | null;
  period_end: string | null;
  observed_at: string | null;
  total: string;
  allocated: string;
  unallocated: string;
  reconciles: boolean;
  /** Findings not listed because they cost less than a cent. */
  below_threshold: number;
  /** The same findings named, with amounts - worded in Python. */
  below_threshold_note: string;
  /** Rendered by present/cost.py and displayed verbatim. The caveat that has
   *  to travel with a cost figure is one decision with one implementation. */
  period_note: string;
  allocation_note: string;
  services: BilledService[];
}

export interface ProfilesResponse {
  profiles: string[];
  path: string;
  exists: boolean;
}

export interface ScanStatus {
  state: ScanState;
  started_at: string | null;
  finished_at: string | null;
  phase: string | null;
  stage: string | null;
  region: string | null;
  collector: string | null;
  steps_done: number;
  steps_total: number;
  error: string | null;
  /** A stop was requested and the scan has not ended yet. */
  stopping: boolean;
  result: { rows?: number; stale?: number; errors?: number } | null;
  log: string[];
}

/** The saved region list for the last scan's account. */
export interface DefaultRegions {
  account: string;
  regions: string[];
  updated_at: string;
  all_regions_scan_at: string;
}

export interface ScanRequest {
  regions?: string[] | null;
  all_regions?: boolean;
  profile?: string | null;
  no_cost?: boolean;
  debug?: boolean;
}

export interface GraphNode {
  id: string;
  kind: "resource" | "stub";
  service: string;
  label: string;
  hub?: boolean;
}

export interface GraphEdgeLink {
  rel: string;
  conn_type: string;
  evidence: string;
  confidence: string;
  grouping: boolean;
}

export interface GraphEdgeVia {
  service: string;
  label: string;
}

export interface GraphEdge {
  source: string;
  target: string;
  category: "link" | "attribute";
  state: "resolved" | "dangling" | "attribute";
  grouping: boolean;
  confidence: string;
  links: GraphEdgeLink[];
  bridged?: boolean;
  via?: GraphEdgeVia[];
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats: Record<string, number>;
}

/** The architecture diagram's Mermaid source from GET /api/mermaid, rendered in Python. */
export interface MermaidResponse {
  mermaid: string;
}

/** GET /api/architecture-icons — an Iconify icon pack for mermaid.registerIconPacks. */
export interface IconPack {
  prefix: string;
  icons: Record<string, unknown>;
  width?: number;
  height?: number;
}

/** One circle's item (ProjectSummary/ServiceSummary.as_dict() plus `id`). Key on `id`,
 *  never display_name; unused_pct is null when empty; money is a string, never float-add. */
export interface OverviewItem {
  id: string;
  display_name: string;
  kind: "project" | "shared" | "ambiguous" | "unassigned" | "service" | "bill";
  resource_keys: string[];
  resource_count: number;
  unused: number;
  active: number;
  unknown: number;
  unused_pct: number | null;
  all_unknown: boolean;
  why: string;
  /** Projects only. */
  project_id?: string;
  grouping_method?: string;
  /** Services and bill lines only: the resource types, for the colour. */
  resource_types?: string[];
  /** Services only: [resource type, count], largest first. */
  type_counts?: [string, number][];
  /** Bill lines only. */
  cost?: string | null;
}

/** One circle, in unit space — mirrors present/bubbles/pack.Bubble.
 *  Positions come from Python; nothing here computes a layout. */
export interface Bubble {
  id: string;
  x: number;
  y: number;
  r: number;
  value: number;
}

/** One view of the overview ("service" or "project"). `packs` and `lists`
 *  are keyed by metric id: the circles to draw and the side list to show. */
export interface OverviewView {
  id: string;
  label: string;
  noun: string;
  items: OverviewItem[];
  metrics: [string, string, string][];
  packs: Record<string, Bubble[]>;
  lists: Record<string, string[]>;
  notes: string[];
}

/** GET /api/overview — present/bubbles/overview.overview_payload, which is
 *  also what the CLI's aws_inventory.html embeds. */
export interface Overview {
  schema_version: number;
  views: OverviewView[];
  default_view: string;
  default_metric: string;
  currency: string;
  period_start: string;
  period_end: string;
  cost_queried: boolean;
  scanned_at: string;
  account: string;
}

export interface ServiceMetaEntry {
  category: string;
  color: string;
  /** The AWS service the type is filed under (EC2, S3...), for the filter. */
  service: string;
  [key: string]: unknown;
}

export type ServiceMeta = Record<string, ServiceMetaEntry>;

export interface GroupMember {
  member_key: string;
  present: boolean;
}

/** One project, from GET /api/projects (naming/project_list.py). */
export interface Project {
  project_id: string;
  name: string;
  name_kind: "default" | "assigned";
  sources: string[];
  resource_count: number;
  /** Set when the name is assigned: what rename and forget act on. */
  group_id: string | null;
  /** A member to seed a first assigned name with. */
  seed_key: string | null;
  manual: boolean;
  saved_members: number;
  live_members: number;
  updated_at: string | null;
  /** Why a saved name is shown nowhere, or "". */
  not_applied: string;
}

export interface Group {
  id: string;
  name: string;
  updated_at: string | null;
  members: GroupMember[];
  live_members: number;
  /** A hand-assembled group: its membership is exactly its stored list and never
   *  auto-extends. */
  manual: boolean;
}

export type ConnectionState = "on" | "report-only" | "off";

export interface ConnectionTypeInfo {
  id: string;
  kind: string;
  default_state: ConnectionState;
  state: ConnectionState;
  source: string;
  targets: string[];
  mechanism: string;
  confidence: string | null;
  description: string;
}

export interface ConfigResponse {
  raw: Record<string, unknown>;
  path: string;
  exists: boolean;
  authoritative_only: boolean;
  graph_in_html: boolean;
  graph_all_nodes: boolean;
  bubbles_in_html: boolean;
  bubbles_default_metric: string;
  snapshot_file: string;
  output_dir: string;
  assume_role: boolean;
  role_name: string;
  connection_types: ConnectionTypeInfo[];
}

export interface ConfigUpdate {
  connection_types: Record<string, ConnectionState>;
  authoritative_only: boolean;
  graph_in_html: boolean;
  graph_all_nodes: boolean;
  bubbles_in_html: boolean;
  bubbles_default_metric: string;
  snapshot_file: string;
  assume_role: boolean;
  role_name: string;
}

/** One uploaded document. `chunks` null = not indexed since it arrived; 0 = indexed
 *  but nothing readable. */
export interface UploadedDocument {
  name: string;
  size: number;
  uploaded: string;
  chunks: number | null;
}

export interface DocumentUploadResult {
  documents: UploadedDocument[];
  replaced: string[];
}

/** One agent turn. `session_id` is a client-held conversation id (no auth);
 *  `resource_keys` are the rows in scope, in rowKey() form. */
export interface AgentQueryRequest {
  session_id: string;
  message: string;
  resource_keys: string[];
}

/** Whether the agent can run. A presence check only: a bad key fails on the first
 *  question. */
export interface AgentStatus {
  available: boolean;
  reason: string;
}
