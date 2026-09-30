"""FastAPI entrypoint for the backend.

Creates the PostgreSQL engine (chat history), AgentService and the MCP
server objects on startup and exposes them on ``app.state``.

Run from the project root:
    uvicorn apps.backend.app.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Literal, Optional

from dotenv import load_dotenv

# The Agents SDK reads os.environ directly (e.g. OPENAI_API_KEY), so load .env first.
load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from agents import RunResult
from agents.tool import HostedMCPTool

from apps.backend.app.auth import require_api_key
from apps.backend.app.config import get_settings
from apps.backend.services.agent_service import AgentService
from apps.backend.services.approval_service import (
    ApprovalDecision,
    ApprovalError,
    InMemoryPendingApprovalStore,
    PausedRun,
    park_paused_run,
    pending_approvals,
)
from apps.backend.services.chat_history_service import create_engine, get_chat_session
from apps.backend.services.chat_stream_service import SSE_HEADERS, sse_chat_stream
from apps.backend.services.load_mcp_service import MCPServerConfigService

settings = get_settings()

# Static chat UI (apps/frontend), served at /ui so it shares the API's origin.
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if not settings.api_keys:
        logger.warning("API_KEYS is not set: API key authentication is DISABLED")

    engine = create_engine(settings)

    # MCP servers are only instantiated here; AgentService connects them per run.
    mcp_servers = MCPServerConfigService().load(settings.mcp_servers_spec())
    logger.info("Loaded %d MCP server(s)", len(mcp_servers))

    app.state.engine = engine
    app.state.agent_service = AgentService(
        name=settings.AGENT_NAME,
        model=settings.AGENT_MODEL,
        max_turns=settings.AGENT_MAX_TURNS,
    )
    app.state.mcp_servers = mcp_servers
    app.state.approval_store = InMemoryPendingApprovalStore(settings.APPROVAL_TTL_SECONDS)

    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1)
    message: str = Field(min_length=1)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class ApprovalRequest(BaseModel):
    """A tool call the agent wants to make and needs the user's OK for."""

    approval_id: str
    tool_name: str
    server_name: Optional[str] = None
    arguments: Any = None


class ChatResponse(BaseModel):
    """Either the final ``reply``, or ``approvals`` to decide on via ``/chat/approvals``."""

    session_id: str
    status: Literal["completed", "approval_required"]
    reply: Optional[str] = None
    approvals: list[ApprovalRequest] = []


class ApprovalDecisionRequest(BaseModel):
    approval_id: str = Field(min_length=1)
    approved: bool
    reason: Optional[str] = None


class ApprovalsBody(BaseModel):
    session_id: str = Field(min_length=1)
    decisions: list[ApprovalDecisionRequest] = Field(min_length=1)


async def _to_chat_response(session_id: str, result: RunResult, state) -> ChatResponse:
    """Build the response for a finished or paused run, parking a paused run for later."""
    if not result.interruptions:
        return ChatResponse(
            session_id=session_id, status="completed", reply=str(result.final_output)
        )
    paused = PausedRun(
        state=result.to_state().to_string(),
        approvals=pending_approvals(result.interruptions),
    )
    await state.approval_store.put(session_id, paused)
    return ChatResponse(
        session_id=session_id,
        status="approval_required",
        approvals=[ApprovalRequest(**a.to_dict()) for a in paused.approvals],
    )


def _stream_response(session_id: str, events, state) -> StreamingResponse:
    return StreamingResponse(
        sse_chat_stream(
            session_id,
            park_paused_run(events, session_id, state.approval_store),
            heartbeat_seconds=settings.SSE_HEARTBEAT_SECONDS,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


async def _ensure_not_paused(state, session_id: str) -> None:
    if await state.approval_store.has(session_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This session is waiting for tool approval; answer it via /chat/approvals first.",
        )


async def _take_paused_run(
    body: ApprovalsBody, state
) -> tuple[PausedRun, list[ApprovalDecision]]:
    """Claim the session's paused run and check the decisions against it.

    Claiming is atomic, so an approval is used at most once: after this
    point the tool may run, and it must never be approvable a second time. Only
    a bad request (nothing has run yet) leaves the run paused for a retry.
    """
    paused = await state.approval_store.pop(body.session_id)
    if paused is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No pending tool approval for this session."
        )
    decisions = [
        ApprovalDecision(d.approval_id, d.approved, d.reason) for d in body.decisions
    ]
    try:
        paused.check(decisions)
    except ApprovalError as exc:
        await state.approval_store.put(body.session_id, paused)
        raise HTTPException(422, str(exc)) from exc
    return paused, decisions


@app.post("/chat", tags=["chat"], dependencies=[Depends(require_api_key)])
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    """One chat turn ; history for ``session_id`` is loaded and saved in PostgreSQL.

    If the agent wants to call a tool that requires approval, the turn pauses
    and the response lists the pending approvals; answer them with
    ``POST /chat/approvals``.
    """
    state = request.app.state
    await _ensure_not_paused(state, body.session_id)
    session = get_chat_session(
        body.session_id, state.engine, create_tables=settings.DB_CREATE_TABLES
    )
    result = await state.agent_service.run(
        body.message, session, mcp_servers=state.mcp_servers
    )
    return await _to_chat_response(body.session_id, result, state)


@app.post(
    "/chat/stream",
    tags=["chat"],
    dependencies=[Depends(require_api_key)],
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def chat_stream(body: ChatRequest, request: Request) -> StreamingResponse:
    """One chat turn as a Server-Sent Events stream.

    Same request as ``POST /chat``. Emits ``run_started``, then ``text_delta`` /
    ``reasoning_delta`` / ``tool_call`` / ``tool_output`` / ``message`` events as
    the agent works, and finally ``done`` (or ``error``, or ``approval_required``
    when a tool needs approval: answer it with ``POST /chat/approvals/stream``).
    See ``docs/chat-stream-protocol.md``. Disconnecting cancels the run.
    """
    state = request.app.state
    await _ensure_not_paused(state, body.session_id)
    session = get_chat_session(
        body.session_id, state.engine, create_tables=settings.DB_CREATE_TABLES
    )
    events = state.agent_service.run_stream(
        body.message, session, mcp_servers=state.mcp_servers
    )
    return _stream_response(body.session_id, events, state)


@app.post("/chat/approvals", tags=["chat"], dependencies=[Depends(require_api_key)])
async def resolve_approvals(body: ApprovalsBody, request: Request) -> ChatResponse:
    """Approve or reject every pending tool call of a session and continue the turn.

    The response has the same shape as ``/chat``: the agent may finish, or
    pause again on another tool that needs approval.
    """
    state = request.app.state
    paused, decisions = await _take_paused_run(body, state)
    session = get_chat_session(body.session_id, state.engine)
    result = await state.agent_service.resume(
        paused, decisions, session, mcp_servers=state.mcp_servers
    )
    return await _to_chat_response(body.session_id, result, state)


@app.post(
    "/chat/approvals/stream",
    tags=["chat"],
    dependencies=[Depends(require_api_key)],
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def resolve_approvals_stream(
    body: ApprovalsBody, request: Request
) -> StreamingResponse:
    """``POST /chat/approvals`` with the continued turn streamed as SSE.

    Same events as ``/chat/stream``. Decisions that do not match the pending
    approvals fail with ``422`` before the stream starts and keep the run paused.
    """
    state = request.app.state
    paused, decisions = await _take_paused_run(body, state)
    session = get_chat_session(body.session_id, state.engine)
    events = state.agent_service.resume_stream(
        paused, decisions, session, mcp_servers=state.mcp_servers
    )
    return _stream_response(body.session_id, events, state)


@app.get("/health", tags=["health"])
async def health(request: Request, response: Response) -> dict:
    """Report database connectivity and which services / MCP servers are loaded."""
    state = request.app.state
    try:
        async with state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        database = {"connected": True}
    except Exception as exc:
        logger.warning("Database health check failed: %s", exc)
        database = {"connected": False}

    mcp_servers = [
        {
            "name": s.tool_config["server_label"] if isinstance(s, HostedMCPTool) else s.name,
            "type": type(s).__name__,
        }
        for s in state.mcp_servers
    ]

    healthy = database["connected"]
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if healthy else "degraded",
        "version": settings.APP_VERSION,
        "database": database,
        "services": {
            "agent_service": state.agent_service is not None,
        },
        "mcp_servers": mcp_servers,
    }


if FRONTEND_DIR.is_dir():

    @app.get("/", include_in_schema=False)
    async def index() -> RedirectResponse:
        return RedirectResponse("/ui/")

    app.mount("/ui", StaticFiles(directory=FRONTEND_DIR, html=True), name="ui")
