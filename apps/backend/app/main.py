"""FastAPI entrypoint for the backend.

Initializes the database client, ChatHistoryService, AgentService and the MCP
server objects on startup and exposes them on ``app.state``.

Run from the project root:
    uvicorn apps.backend.app.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from dotenv import load_dotenv

# DatabaseConfig and the Agents SDK read os.environ directly, so load .env first.
load_dotenv()

from fastapi import FastAPI, Request, Response, status

from agents.tool import HostedMCPTool

from apps.backend.app.config import get_settings
from apps.backend.services.agent_service import AgentService
from apps.backend.services.chat_history_service import ChatHistoryService
from common.load_mcp_service import MCPServerConfigService
from db.client import DatabaseClient

settings = get_settings()

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db_client = DatabaseClient()
    if settings.DB_CREATE_TABLES:
        await db_client.create_tables()

    # MCP servers are only instantiated here; AgentService connects them per run.
    mcp_servers = MCPServerConfigService().load(settings.mcp_servers_spec())
    logger.info("Loaded %d MCP server(s)", len(mcp_servers))

    app.state.db_client = db_client
    app.state.chat_history_service = ChatHistoryService(db=db_client)
    app.state.agent_service = AgentService(
        name=settings.AGENT_NAME,
        model=settings.AGENT_MODEL,
        max_turns=settings.AGENT_MAX_TURNS,
    )
    app.state.mcp_servers = mcp_servers

    try:
        yield
    finally:
        await db_client.disconnect()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)


@app.get("/health", tags=["health"])
async def health(request: Request, response: Response) -> dict:
    """Report database connectivity and which services / MCP servers are loaded."""
    state = request.app.state
    database = await state.db_client.check_health()

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
            "chat_history_service": state.chat_history_service is not None,
        },
        "mcp_servers": mcp_servers,
    }
