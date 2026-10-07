// The architecture diagram: the Mermaid source GET /api/mermaid renders in Python
// (the same the CLI writes to .mmd), turned into an SVG here.

import { useEffect, useRef, useState } from "react";
import mermaid from "mermaid";
import { api } from "../api/client";

// Matches present/graph/diagram.py's DIAGRAM_JS exactly, so the two views
// render identically rather than merely similarly.
mermaid.initialize({
  startOnLoad: false,
  theme: "neutral",
  maxTextSize: 500000,
  flowchart: { useMaxWidth: false, htmlLabels: true, curve: "basis" },
});

// Nodes name icons as "aws:lambda"; the pack is fetched once and registered before
// the first render. Without it Mermaid still draws, with placeholder icons.
const iconsReady: Promise<void> = api.getArchitectureIcons()
  .then((pack) => {
    type IconPackEntry = Parameters<typeof mermaid.registerIconPacks>[0][number];
    mermaid.registerIconPacks([{ name: pack.prefix, icons: pack } as unknown as IconPackEntry]);
  })
  .catch(() => undefined);

let renderCount = 0;

interface Props {
  source: string | null;
  /** Projects the diagram can be scoped to, in the overview's order. */
  projects: { id: string; name: string }[];
  /** The chosen scope, or null for the whole account. The SCOPING happens in
   *  Python (analyze/graph.extract_project) before the source arrives. */
  projectId: string | null;
  onProjectChange: (projectId: string | null) => void;
}

export function ArchitecturePanel({ source, projects, projectId, onProjectChange }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!source || !container.current) return;
    setError(null);
    const target = container.current;
    const id = `architecture-diagram-${++renderCount}`;
    let cancelled = false;
    iconsReady
      .then(() => mermaid.render(id, source))
      .then(({ svg }) => { if (!cancelled) target.innerHTML = svg; })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => { cancelled = true; };
  }, [source]);

  if (!source) {
    return <p className="empty">No architecture diagram yet.</p>;
  }

  return (
    <section className="graph-panel architecture-panel">
      <div className="graph-controls">
        <span>
          The application&rsquo;s runtime shape: solid lines are calls and data use;
          dotted lines are likely but unproven (IAM access, a name match). Use the
          Graph tab for every link, including what only groups resources together.
        </span>
        {/* Same control as the report's Diagram tab: hidden for one project,
            whose diagram is the account's. */}
        {projects.length > 1 && (
          <label className="architecture-scope">
            Scope{" "}
            <select
              value={projectId ?? ""}
              onChange={(e) => onProjectChange(e.target.value || null)}
            >
              <option value="">Whole account</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </label>
        )}
        <a
          className="linkish download"
          href={projectId
            ? `/api/export/architecture.mmd?project_id=${encodeURIComponent(projectId)}`
            : "/api/export/architecture.mmd"}
          download
        >
          Download .mmd
        </a>
        <a
          className="linkish download"
          href={projectId
            ? `/api/export/architecture.drawio?project_id=${encodeURIComponent(projectId)}`
            : "/api/export/architecture.drawio"}
          download
        >
          Download .drawio
        </a>
      </div>
      {error ? (
        <p className="empty">
          This diagram could not be rendered: {error}. The same graph is in the Graph tab.
        </p>
      ) : (
        <div className="diagram-scroll" ref={container} />
      )}
    </section>
  );
}
