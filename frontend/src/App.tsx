// The application shell: what is loaded, when, and which tab shows.
// A PAGE LOAD NEVER SCANS: it reads the last scan; only the Scan button scans.

import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "./api/client";
import type {
  BillingSummary, ConfigResponse, Graph, Group, Overview, Project, Row,
  ScanSummary, ServiceMeta,
} from "./api/types";
import { BillingBanner } from "./components/BillingBanner";
import { ScanBar } from "./components/ScanBar";
import { ResourceTable } from "./components/ResourceTable";
import { GraphPanel } from "./components/GraphPanel";
import { ArchitecturePanel } from "./components/ArchitecturePanel";
import { OverviewPanel } from "./components/OverviewPanel";
import { ProjectsPage } from "./pages/ProjectsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { AgentPanel } from "./components/AgentPanel";

type Tab =
  | "overview" | "resources" | "graph" | "architecture" | "projects" | "settings";

// Overview first: "what is in this account, and what looks idle" comes before
// the per-resource question.
const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "resources", label: "Resources" },
  { id: "graph", label: "Graph" },
  { id: "architecture", label: "Architecture" },
  { id: "projects", label: "Projects" },
  { id: "settings", label: "Settings" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("overview");
  const [scan, setScan] = useState<ScanSummary | null>(null);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [billing, setBilling] = useState<BillingSummary | null>(null);
  const [serviceMeta, setServiceMeta] = useState<ServiceMeta>({});
  const [groups, setGroups] = useState<Group[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  // Starts false so the panel never flashes in and out on load: the
  // uncertain state and the unavailable one should look the same.
  const [agentAvailable, setAgentAvailable] = useState(false);
  const [allNodes, setAllNodes] = useState(false);
  const [focusKey, setFocusKey] = useState<string | null>(null);
  // Checkbox multi-select, lifted here so it survives tab switches and can also
  // scope an agent question. ResourceTable decides what to do with it.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [overview, setOverview] = useState<Overview | null>(null);
  // The Architecture tab's own scope. Nothing else sets or reads it.
  const [architectureProject, setArchitectureProject] = useState<string | null>(null);
  const [mermaidSrc, setMermaidSrc] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const loadScan = useCallback(async () => {
    try {
      setScan(await api.getScan());
      setNotice(null);
    } catch (e) {
      // No scan yet is the app's first-run state, not a failure. It should read
      // as an invitation to press Scan.
      if (e instanceof ApiError && e.isMissingScan) {
        setScan(null);
        setNotice(null);
      } else {
        setNotice(e instanceof ApiError ? e.message : String(e));
      }
    }
  }, []);

  // Same snapshot as loadScan, but already classified and worded by Python.
  const loadBilling = useCallback(async () => {
    try { setBilling(await api.getBilling()); } catch { setBilling(null); }
  }, []);

  const loadGraph = useCallback(async () => {
    try {
      setGraph(await api.getGraph(allNodes));
    } catch {
      setGraph(null);
    }
  }, [allNodes]);

  // The summaries AND the packed circle positions, both computed in Python -
  // see components/OverviewPanel.tsx for why neither is derived here.
  const loadOverview = useCallback(async () => {
    try { setOverview(await api.getOverview()); } catch { setOverview(null); }
  }, []);

  // Saved names (for "add to existing project") and the project list together.
  const loadGroups = useCallback(async () => {
    try { setGroups(await api.getGroups()); } catch { /* listed empty */ }
    try { setProjects(await api.getProjects()); } catch { /* listed empty */ }
  }, []);

  const loadConfig = useCallback(async () => {
    try {
      setConfig(await api.getConfig());
    } catch (e) {
      // A broken audit_config.json must not take the whole app down - the
      // Settings tab is where it gets fixed, so it has to stay reachable.
      setNotice(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  // Rendered server-side by render_mermaid(), like the CLI's .mmd; a new scope
  // is a new request, not a client-side filter.
  const loadMermaid = useCallback(async () => {
    try {
      setMermaidSrc((await api.getMermaid(architectureProject)).mermaid);
    } catch {
      setMermaidSrc(null);
    }
  }, [architectureProject]);

  // Everything, once, on mount. Never a scan.
  useEffect(() => {
    void (async () => {
      await Promise.all([loadScan(), loadGroups(), loadConfig(),
                         loadBilling(), loadOverview()]);
      try { setServiceMeta(await api.getServiceMeta()); } catch { /* colours fall back */ }
      // Asked once: availability depends on a secret read at container start.
      // A failed call hides the agent.
      try { setAgentAvailable((await api.getAgentStatus()).available); }
      catch { setAgentAvailable(false); }
      setLoading(false);
    })();
  }, [loadScan, loadGroups, loadConfig, loadBilling, loadOverview]);

  // The graph is fetched separately because the all_nodes toggle refetches it.
  useEffect(() => { void loadGraph(); }, [loadGraph]);

  useEffect(() => { void loadMermaid(); }, [loadMermaid]);

  /** Called by ScanBar when a scan stops running. Re-reads everything a scan
   *  can have changed - which is all of it except the config. */
  const onScanFinished = useCallback(() => {
    void loadScan();
    void loadGraph();
    void loadGroups();
    void loadMermaid();
    void loadBilling();
    void loadOverview();
  }, [loadScan, loadGraph, loadGroups, loadMermaid, loadBilling, loadOverview]);

  const rows: Row[] = scan?.rows ?? [];

  // Real projects with resources, from the served By project view - the same
  // set render.py writes a per-project diagram for. Buckets are not architectures.
  const architectureProjects = (overview?.views.find((v) => v.id === "project")?.items ?? [])
    .filter((p) => p.kind === "project" && p.resource_count > 0)
    .map((p) => ({ id: p.id, name: p.display_name }));
  // A scope whose project has gone (renamed away, forgotten, rescanned) reads
  // as the whole account rather than as a stale id.
  const architectureScope = architectureProjects.some((p) => p.id === architectureProject)
    ? architectureProject : null;
  useEffect(() => {
    if (overview && architectureProject && !architectureScope) setArchitectureProject(null);
  }, [overview, architectureProject, architectureScope]);

  // Naming or regrouping changes project membership, and with it the overview.
  // The overview used to be left stale here until the next scan.
  const onGroupsChanged = () => { void loadGroups(); void loadScan(); void loadOverview(); };

  // What the agent is asked about: the checkbox selection, else the focused
  // resource (e.g. a clicked graph node), else nothing.
  const resourceKeys: string[] =
    selected.size > 0 ? [...selected] : focusKey ? [focusKey] : [];

  return (
    <div className="app">
      <header>
        <h1>AWS resource audit</h1>
        <nav>
          {TABS.map((t) => (
            <button
              key={t.id}
              className={tab === t.id ? "tab active" : "tab"}
              onClick={() => setTab(t.id)}
            >
              {t.label}
              {t.id === "projects" && projects.length > 0 && (
                <span className="badge">{projects.length}</span>
              )}
            </button>
          ))}
        </nav>
      </header>

      <ScanBar
        onFinished={onScanFinished}
        scannedAt={scan?.scanned_at ?? null}
        account={scan?.account ?? ""}
        rowCount={scan?.row_count ?? 0}
      />

      {notice && <div className="banner error">{notice}</div>}

      {/* AgentPanel sits beside <main>, not inside the tab ternary below, so
          it survives a tab switch instead of remounting with fresh state. */}
      <div className="app-body">
        <main>
          {loading ? (
            <p className="empty">Loading…</p>
          ) : tab === "overview" ? (
            scan ? (
              <>
                <BillingBanner billing={billing} />
                <OverviewPanel overview={overview} serviceMeta={serviceMeta} />
              </>
            ) : (
              <FirstRun />
            )
          ) : tab === "resources" ? (
            scan ? (
              <>
                <BillingBanner billing={billing} />
                <ResourceTable
                  rows={rows}
                  serviceMeta={serviceMeta}
                  focusKey={focusKey}
                  onFocusKey={setFocusKey}
                  onClearFocus={() => setFocusKey(null)}
                  namedGroups={groups}
                  projects={projects}
                  onGroupsChanged={onGroupsChanged}
                  selected={selected}
                  setSelected={setSelected}
                />
              </>
            ) : (
              <FirstRun />
            )
          ) : tab === "graph" ? (
            graph && scan ? (
              <GraphPanel
                graph={graph}
                rows={rows}
                serviceMeta={serviceMeta}
                allNodes={allNodes}
                onAllNodesChange={setAllNodes}
                onPickNode={setFocusKey}
              />
            ) : (
              <FirstRun />
            )
          ) : tab === "architecture" ? (
            scan ? (
              <ArchitecturePanel
                source={mermaidSrc}
                projects={architectureProjects}
                projectId={architectureScope}
                onProjectChange={setArchitectureProject}
              />
            ) : (
              <FirstRun />
            )
          ) : tab === "projects" ? (
            <ProjectsPage projects={projects} onChanged={onGroupsChanged} />
          ) : config ? (
            <SettingsPage
              config={config}
              onSaved={() => { void loadConfig(); void loadGraph(); }}
            />
          ) : (
            <p className="empty">Settings could not be read — see the message above.</p>
          )}
        </main>
        {agentAvailable && <AgentPanel resourceKeys={resourceKeys} />}
      </div>
    </div>
  );
}

function FirstRun() {
  return (
    <div className="first-run">
      <h2>No scan yet</h2>
      <p>
        Press <strong>Scan</strong> above. It reads your AWS account across the
        regions you choose, works out what is stale and what depends on what,
        and writes a snapshot everything here renders from.
      </p>
      <p className="hint">
        A full scan takes minutes and makes one chargeable Cost Explorer request
        (~$0.01). Tick <em>Skip cost lookup</em> to avoid it. Nothing here ever
        writes to your AWS account.
      </p>
    </div>
  );
}
