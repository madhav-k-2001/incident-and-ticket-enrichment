"""FastAPI entrypoint for the backend.

Creates the PostgreSQL engine (chat history), AgentService and the MCP
server objects on startup and exposes them on ``app.state``.

Run from the project root:
    uvicorn apps.backend.app.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal, Optional

from dotenv import load_dotenv

# The Agents SDK reads os.environ directly (e.g. OPENAI_API_KEY), so load .env first.
load_dotenv()

from fastapi import FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from agents import RunResult
from agents.tool import HostedMCPTool

from apps.backend.app.config import get_settings
from apps.backend.services.agent_service import AgentService
from apps.backend.services.approval_service import (
    ApprovalDecision,
    ApprovalError,
    InMemoryPendingApprovalStore,
    pending_approvals,
)
from apps.backend.services.chat_history_service import create_engine, get_chat_session
from apps.backend.services.load_mcp_service import MCPServerConfigService

settings = get_settings()

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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
    await state.approval_store.put(session_id, result.to_state().to_string())
    return ChatResponse(
        session_id=session_id,
        status="approval_required",
        approvals=[
            ApprovalRequest(
                approval_id=a.id,
                tool_name=a.tool_name,
                server_name=a.server_name,
                arguments=a.arguments,
            )
            for a in pending_approvals(result.interruptions)
        ],
    )


@app.post("/chat", tags=["chat"])
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    """One chat turn; history for ``session_id`` is loaded and saved in PostgreSQL.

    If the agent wants to call a tool that requires approval, the turn pauses
    and the response lists the pending approvals; answer them with
    ``POST /chat/approvals``.
    """
    state = request.app.state
    if await state.approval_store.has(body.session_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This session is waiting for tool approval; answer it via /chat/approvals first.",
        )
    session = get_chat_session(
        body.session_id, state.engine, create_tables=settings.DB_CREATE_TABLES
    )
    result = await state.agent_service.run(
        body.message, session, mcp_servers=state.mcp_servers
    )
    return await _to_chat_response(body.session_id, result, state)


@app.post("/chat/approvals", tags=["chat"])
async def resolve_approvals(body: ApprovalsBody, request: Request) -> ChatResponse:
    """Approve or reject every pending tool call of a session and continue the turn.

    The response has the same shape as ``/chat``: the agent may finish, or
    pause again on another tool that needs approval.
    """
    state = request.app.state
    paused = await state.approval_store.pop(body.session_id)
    if paused is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No pending tool approval for this session."
        )
    decisions = [
        ApprovalDecision(d.approval_id, d.approved, d.reason) for d in body.decisions
    ]
    session = get_chat_session(body.session_id, state.engine)
    try:
        result = await state.agent_service.resume(
            paused, decisions, session, mcp_servers=state.mcp_servers
        )
    except ApprovalError as exc:
        # A bad request must not lose the paused run: let the user try again.
        await state.approval_store.put(body.session_id, paused)
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        await state.approval_store.put(body.session_id, paused)
        raise
    return await _to_chat_response(body.session_id, result, state)


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
