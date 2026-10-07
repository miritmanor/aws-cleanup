"""The agent endpoint: validates the request and streams run_turn as Server-Sent
Events, one JSON frame each: {"token": ...} or a final {"error": ...}."""

import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from aws_resource_audit.errors import AuditError

from .. import deps
from ..agent import agent_loop, llm
from ..agent.settings import load_agent_settings
from ..schemas import AgentQueryRequest, AgentStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["agent"])

# Shown when a turn fails with anything but AuditError; the traceback goes to the log.
GENERIC_ERROR = ("Sorry - something went wrong answering that. Try again, and "
                 "if it keeps happening check the backend log "
                 "(`docker compose logs backend`).")


def _frame(payload: dict) -> str:
    """One SSE event. JSON-encoded rather than raw text because a token can
    contain newlines, and a bare newline inside `data:` would split it."""
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/agent/query", summary="Ask the resource Q&A agent a question")
def query_agent(request: AgentQueryRequest):
    """Stream the reply as SSE (read via fetch, since EventSource is GET-only). Errors
    travel as a final frame: the 200 is already sent when the body starts."""
    async def stream():
        try:
            async for token in agent_loop.run_turn(
                request.session_id, request.message, request.resource_keys,
            ):
                yield _frame({"token": token})
        except AuditError as exc:
            # Expected and actionable: no API key, unusable agent config. The
            # message is written for the user to act on.
            logger.warning("agent turn failed for session %s: %s",
                           request.session_id, exc)
            yield _frame({"error": str(exc)})
        except Exception:                                    # noqa: BLE001
            # A bug: traceback to the server log, a usable sentence to the client.
            logger.exception("agent turn crashed for session %s",
                             request.session_id)
            yield _frame({"error": GENERIC_ERROR})
        # Exception, not BaseException: Starlette cancels this generator to
        # signal a client disconnect, and that must propagate, not be framed.

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/agent/status", response_model=AgentStatus,
            summary="Can the agent answer anything")
def agent_status():
    """Whether the agent is usable. Never fails: a bad config reads as unavailable. A
    presence check only; a wrong key fails at the first question."""
    try:
        settings = load_agent_settings(deps.agent_config_path())
    except AuditError as exc:
        return AgentStatus(available=False, reason=str(exc))
    reason = llm.unavailable_reason(settings)
    return AgentStatus(available=not reason, reason=reason)
