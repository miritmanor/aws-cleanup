// A persistent, collapsible agent panel beside <main>, so conversations survive tab switches.
// session_id (in localStorage) is a conversation key, not a login; replies stream via SSE.

import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import { AgentDocuments } from "./AgentDocuments";

const SESSION_STORAGE_KEY = "agentSessionId";

interface Message {
  role: "user" | "assistant";
  content: string;
}

function newSessionId(): string {
  const id = crypto.randomUUID();
  localStorage.setItem(SESSION_STORAGE_KEY, id);
  return id;
}

interface Props {
  /** The rows in scope (rowKey() form), sent with every question; empty means none. */
  resourceKeys: string[];
}

export function AgentPanel({ resourceKeys }: Props) {
  const [collapsed, setCollapsed] = useState(true);
  const [sessionId, setSessionId] = useState(
    () => localStorage.getItem(SESSION_STORAGE_KEY) || newSessionId(),
  );
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages]);

  function startNewConversation() {
    setSessionId(newSessionId());
    setMessages([]);
    setError(null);
  }

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setError(null);
    setBusy(true);
    setMessages((prev) => [...prev, { role: "user", content: text }, { role: "assistant", content: "" }]);

    try {
      for await (const token of api.queryAgent({ session_id: sessionId, message: text, resource_keys: resourceKeys })) {
        setMessages((prev) => {
          const next = [...prev];
          next[next.length - 1] = { role: "assistant", content: next[next.length - 1].content + token };
          return next;
        });
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      // The empty assistant placeholder would otherwise sit there forever
      // looking like a reply that came back blank.
      setMessages((prev) => prev.slice(0, -1));
    } finally {
      setBusy(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  }

  if (collapsed) {
    return (
      <aside className="agent-panel collapsed">
        <button className="agent-toggle" onClick={() => setCollapsed(false)} title="Ask the agent">
          Ask the agent
        </button>
      </aside>
    );
  }

  return (
    <aside className="agent-panel">
      <div className="agent-header">
        <strong>Ask the agent</strong>
        <div className="agent-header-actions">
          <button className="linkish" onClick={startNewConversation}>New conversation</button>
          <button className="linkish" onClick={() => setCollapsed(true)} title="Collapse">×</button>
        </div>
      </div>

      {resourceKeys.length > 0 && (
        <div className="agent-scope">
          Asking about {resourceKeys.length === 1 ? "1 selected resource" : `${resourceKeys.length} selected resources`}
        </div>
      )}

      <div className="agent-messages" ref={listRef}>
        {messages.length === 0 && (
          <p className="empty">
            Ask about whether a resource is active or stale, why it was grouped, or how your
            resources relate to each other.
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`agent-message agent-message-${m.role}`}>
            {m.content || (m.role === "assistant" && busy && i === messages.length - 1 ? "…" : "")}
          </div>
        ))}
      </div>

      {error && <div className="banner error">{error}</div>}

      <div className="agent-input-row">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask a question…"
          rows={2}
          disabled={busy}
        />
        <button className="primary" onClick={() => void send()} disabled={busy || !input.trim()}>
          Send
        </button>
      </div>

      {/* The corpus, below the question box and collapsed by default: the chat
          is what the panel is for. */}
      <AgentDocuments />
    </aside>
  );
}
