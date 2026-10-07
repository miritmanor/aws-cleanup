// The agent's corpus (your own documents about your account), folded out at the foot
// of the agent panel. Reindexing runs straight after every upload or delete.

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { UploadedDocument } from "../api/types";

const ACCEPT = ".pdf,.docx,.xlsx,.csv,.md,.txt";

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** null = not indexed since it arrived; 0 = indexed but nothing readable. */
function indexed(chunks: number | null): string {
  if (chunks === null) return "not indexed";
  if (chunks === 0) return "no text found";
  return `${chunks} chunks`;
}

export function AgentDocuments() {
  const [open, setOpen] = useState(false);
  const [documents, setDocuments] = useState<UploadedDocument[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const [picked, setPicked] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [phase, setPhase] = useState<"uploading" | "indexing" | null>(null);
  const busy = phase !== null;

  // Fetched whenever the panel expands, so the summary count is always current.
  const load = useCallback(async () => {
    try { setDocuments(await api.getDocuments()); } catch { /* listed empty */ }
  }, []);

  useEffect(() => { void load(); }, [load]);

  function reset() {
    setPicked([]);
    if (input.current) input.current.value = "";
  }

  function message(e: unknown): string {
    return e instanceof ApiError ? e.message : String(e);
  }

  /** Re-embed the corpus. A failure here is reported separately: the upload or
   *  delete before it already succeeded. */
  async function reindex(after: string) {
    setPhase("indexing");
    try {
      await api.reindexDocuments();
      setNotice(after);
    } catch (e) {
      setError(`${after} The index was not rebuilt: ${message(e)}`);
    } finally {
      setPhase(null);
      void load();
    }
  }

  async function upload() {
    if (!picked.length) return;
    setError(null);
    setNotice(null);
    setPhase("uploading");
    const count = picked.length;
    try {
      const result = await api.uploadDocuments(picked);
      reset();
      // reindex() reports its own failure and never throws, so this catch
      // only ever sees the upload's.
      await reindex(
        result.replaced.length
          ? `Uploaded. Replaced: ${result.replaced.join(", ")}.`
          : `Uploaded ${count} document${count > 1 ? "s" : ""}.`,
      );
    } catch (e) {
      // Nothing was stored (the batch is validated first), so the files stay
      // selected for the user to fix and retry.
      setError(message(e));
      setPhase(null);
    }
  }

  async function remove(name: string) {
    setError(null);
    setNotice(null);
    try {
      await api.deleteDocument(name);
    } catch (e) {
      setError(message(e));
      return;
    }
    await reindex(`Removed ${name}.`);
  }

  return (
    <section className="agent-docs">
      <button
        className="agent-docs-summary"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <span className="agent-docs-caret">{open ? "▾" : "▸"}</span>
        Documents
        <span className="agent-docs-count">{documents.length}</span>
      </button>

      {open && (
        <div className="agent-docs-body">
          <p className="hint">
            What the agent reads besides the scan — architecture notes,
            runbooks, handover docs. PDF, DOCX, XLSX, CSV, Markdown and plain
            text, up to 25 MB each. Account ids are masked out before the
            model sees them. A name that already exists is replaced. Every
            change rebuilds the index — a few seconds, the first one longer
            while the embedding model loads.
          </p>

          <div className="agent-docs-upload">
            <input
              ref={input}
              type="file"
              multiple
              accept={ACCEPT}
              onChange={(e) => setPicked(Array.from(e.target.files ?? []))}
            />
            <button
              className="primary"
              onClick={() => void upload()}
              disabled={busy || !picked.length}
            >
              {phase === "uploading"
                ? "Uploading…"
                : phase === "indexing"
                  ? "Indexing…"
                  : `Upload${picked.length ? ` ${picked.length}` : ""}`}
            </button>
          </div>

          {error && <div className="banner error">{error}</div>}
          {notice && <div className="banner">{notice}</div>}

          {documents.length === 0 ? (
            <p className="empty">
              No documents yet. The agent answers from the scan alone.
            </p>
          ) : (
            <ul className="agent-docs-list">
              {documents.map((doc) => (
                <li key={doc.name}>
                  <div className="agent-docs-name mono" title={doc.name}>{doc.name}</div>
                  <div className="agent-docs-meta muted">
                    {humanSize(doc.size)} · {indexed(doc.chunks)}
                  </div>
                  <button
                    className="agent-docs-remove"
                    onClick={() => void remove(doc.name)}
                    disabled={busy}
                    title={`Remove ${doc.name}`}
                  >
                    ✕
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
