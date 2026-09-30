"""FastAPI entrypoint for the backend.

Creates the PostgreSQL engine (chat history), AgentService and the MCP
server objects on startup and exposes them on ``app.state``.

Run from the project root:
    uvicorn apps.backend.app.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from dotenv import load_dotenv

# The Agents SDK reads os.environ directly (e.g. OPENAI_API_KEY), so load .env first.
load_dotenv()

from fastapi import FastAPI, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from agents.tool import HostedMCPTool

from apps.backend.app.config import get_settings
from apps.backend.services.agent_service import AgentService
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


class ChatResponse(BaseModel):
    session_id: str
    reply: str


@app.post("/chat", tags=["chat"])
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    """One chat turn; history for ``session_id`` is loaded and saved in PostgreSQL."""
    state = request.app.state
    session = get_chat_session(
        body.session_id, state.engine, create_tables=settings.DB_CREATE_TABLES
    )
    result = await state.agent_service.run(
        body.message, session, mcp_servers=state.mcp_servers
    )
    return ChatResponse(session_id=body.session_id, reply=str(result.final_output))


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
