"""How the agent runs: building the graph and run_turn, one concern. Uses plain
datetime.now() for session idle time, not the scan's frozen clock."""

import json
import logging
import threading
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver

from .. import deps, stores
from ..store import SnapshotStore
from . import PAYLOAD_LOGGER, index
from .llm import get_chat_model
from .prompts import build_system_prompt
from .settings import load_agent_settings
from .tools import make_query_resources_tool, make_search_documents_tool

logger = logging.getLogger(__name__)
payload = logging.getLogger(PAYLOAD_LOGGER)

_lock = threading.Lock()
_agent = None
_checkpointer = None
_snapshot_store: Optional[SnapshotStore] = None
_last_active: dict[str, datetime] = {}


def _new_agent():
    """The compiled agent and what a turn needs, created lazily: building a chat model
    at import would break every endpoint when no key is configured. Index opened, never built."""
    global _agent, _checkpointer, _snapshot_store
    settings = load_agent_settings(deps.agent_config_path())
    model = get_chat_model(settings)
    snapshot_store = stores.snapshot_store()
    document_index = index.get_index()
    tools = [
        make_query_resources_tool(snapshot_store),
        make_search_documents_tool(document_index),
    ]
    checkpointer = MemorySaver()
    agent = create_agent(
        model, tools=tools, system_prompt=build_system_prompt(),
        checkpointer=checkpointer,
    )
    _agent, _checkpointer, _snapshot_store = agent, checkpointer, snapshot_store
    return _state(settings.session_ttl_minutes)


def _state(ttl_minutes):
    return _agent, _checkpointer, _snapshot_store, ttl_minutes


def _get_agent():
    with _lock:
        if _agent is None:
            return _new_agent()
        return _state(load_agent_settings(deps.agent_config_path()).session_ttl_minutes)


def reset():
    """Drop the compiled agent and index handle so the next call rebuilds them (tests,
    provider changes)."""
    global _agent, _checkpointer, _snapshot_store
    with _lock:
        _agent, _checkpointer, _snapshot_store = None, None, None
        _last_active.clear()
    index.reset()


def _evict_idle_sessions(checkpointer, ttl_minutes):
    """Drop sessions idle longer than ttl_minutes; checked lazily at turn start."""
    cutoff = datetime.now(timezone.utc).timestamp() - ttl_minutes * 60
    expired = [sid for sid, last in _last_active.items() if last.timestamp() < cutoff]
    for sid in expired:
        checkpointer.delete_thread(sid)
        del _last_active[sid]


def _resolve_resource_context(snapshot_store: SnapshotStore, resource_keys: list[str]) -> str:
    """Masked summaries for resource_keys, or "". Matched against raw rows, but only
    the masked display rows ever reach the model."""
    if not resource_keys:
        return ""
    from ..store import SnapshotMissing

    try:
        raw_rows = snapshot_store.load().rows
    except SnapshotMissing:
        # The user picked resources and the model will not be told about them.
        logger.warning("no snapshot: the %d selected resource(s) will not "
                       "reach the model", len(resource_keys))
        return ""
    from aws_resource_audit.rows import member_key

    raw_by_key = {member_key(row): i for i, row in enumerate(raw_rows)}
    masked_rows = snapshot_store.get_masked_rows()

    found = []
    for key in resource_keys:
        position = raw_by_key.get(key)
        if position is None:
            logger.debug("selected resource %s is not in the current "
                         "snapshot; dropping it", key)
            continue  # e.g. a rescan removed it - drop silently, don't error.
        found.append(masked_rows[position])
    if not found:
        return ""
    return "Currently selected resource(s): " + json.dumps(found) + "\n\n"


async def run_turn(session_id: str, message: str, resource_keys: list[str]) -> AsyncIterator[str]:
    """Run one turn of `session_id`'s conversation, yielding tokens as they arrive.
    `resource_keys` are the rows the UI currently points at."""
    agent, checkpointer, snapshot_store, ttl_minutes = _get_agent()
    # No freshness check here: the index is rebuilt when the corpus changes,
    # by the reindex endpoint, not on the way to an answer.
    _evict_idle_sessions(checkpointer, ttl_minutes)
    _last_active[session_id] = datetime.now(timezone.utc)

    context = _resolve_resource_context(snapshot_store, resource_keys)
    full_message = f"{context}Question: {message}" if context else message

    config = {"configurable": {"thread_id": session_id}}

    # %-args throughout, never f-strings: a tool result here is the whole masked
    # row set, and it must not even be BUILT unless DEBUG is on.
    payload.debug("turn %s question: %s", session_id, full_message)

    reply = []
    completed = False
    try:
        async for event in agent.astream_events(
            {"messages": [{"role": "user", "content": full_message}]},
            config=config, version="v2",
        ):
            kind = event["event"]
            if kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                if chunk.content:
                    reply.append(chunk.content)
                    yield chunk.content
            elif kind == "on_chat_model_start":
                # The full input the model receives (system prompt, history, tool results).
                payload.debug("turn %s -> model: %r",
                              session_id, event["data"].get("input"))
            elif kind == "on_chat_model_end":
                payload.debug("turn %s <- model: %r",
                              session_id, event["data"].get("output"))
            elif kind == "on_tool_start":
                payload.debug("turn %s tool %s input: %r", session_id,
                              event.get("name"), event["data"].get("input"))
            elif kind == "on_tool_end":
                payload.debug("turn %s tool %s output: %r", session_id,
                              event.get("name"), event["data"].get("output"))
        completed = True
    finally:
        # In a finally, so a turn cut short (client disconnect) still logs what it had.
        if payload.isEnabledFor(logging.DEBUG):
            payload.debug("turn %s reply (%s): %s", session_id,
                          "complete" if completed else "interrupted",
                          "".join(reply))
