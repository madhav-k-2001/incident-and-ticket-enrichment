"""MCP interface layer: tool definitions, input contracts and error mapping.

Tools stay thin: validate input (via type hints), bind a trace id, delegate to
TicketingService, and translate domain errors into MCP tool errors.
"""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, StringConstraints

from ticketing_mcp.client import TicketingApiClient
from ticketing_mcp.config import Settings
from ticketing_mcp.errors import TicketingApiError
from ticketing_mcp.models import Severity, Status, TicketChanges, TicketDraft, TicketList, WriteOutcome
from ticketing_mcp.observability import bind_trace_id
from ticketing_mcp.service import TicketingService

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
Incident ticketing tools.
- Use `search_similar_tickets` to find historical cases and their resolutions.
- Use `find_tickets` to fetch a ticket by id or list tickets by asset/status/severity.
- `create_ticket` and `update_ticket` write immediately. The calling application decides
  whether the operator must approve them first; if a call is rejected, do not retry it.
Pass `trace_id` in the request `_meta` to correlate calls; otherwise one is generated.
"""

# ---- Shared parameter types -------------------------------------------------

# Strict pattern: the id is interpolated into a URL path.
TicketId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,32}$")]


def create_server(settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> MCPServer:
    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[TicketingService]:
        async with TicketingApiClient(settings, transport=transport) as api:
            yield TicketingService(api)

    mcp = MCPServer("ticketing", version="0.1.0", instructions=INSTRUCTIONS, lifespan=lifespan)

    @mcp.tool(annotations=ToolAnnotations(title="Find tickets", read_only_hint=True))
    async def find_tickets(
        ctx: Context,
        ticket_id: Annotated[
            TicketId | None, Field(description="Exact ticket id, e.g. 'INC-1042'. When set, other filters are ignored.")
        ] = None,
        asset_ids: Annotated[list[str] | None, Field(description="Only tickets for these assets, e.g. correlated assets.")] = None,
        status: Status | None = None,
        severity: Severity | None = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> TicketList:
        """Fetch a ticket by id, or list tickets filtered by asset(s), status and severity."""
        async with tool_call(ctx, "find_tickets") as service:
            return await service.find_tickets(
                ticket_id=ticket_id, asset_ids=asset_ids, status=status, severity=severity, limit=limit
            )

    @mcp.tool(annotations=ToolAnnotations(title="Search similar tickets", read_only_hint=True))
    async def search_similar_tickets(
        ctx: Context,
        query: Annotated[str, Field(min_length=3, max_length=500, description="Symptom, cause or alarm description.")],
        asset_id: Annotated[str | None, Field(description="Restrict to one asset, e.g. 'CMP-201'.")] = None,
        limit: Annotated[int, Field(ge=1, le=20)] = 5,
    ) -> TicketList:
        """Rank historical tickets by relevance to a symptom or cause; includes root cause and resolution notes."""
        async with tool_call(ctx, "search_similar_tickets") as service:
            return await service.search_similar_tickets(query, asset_id=asset_id, limit=limit)

    @mcp.tool(annotations=ToolAnnotations(title="Create ticket", read_only_hint=False, idempotent_hint=False))
    async def create_ticket(ctx: Context, draft: TicketDraft) -> WriteOutcome:
        """Create an incident ticket."""
        async with tool_call(ctx, "create_ticket") as service:
            return await service.create_ticket(draft)

    @mcp.tool(annotations=ToolAnnotations(title="Update ticket", read_only_hint=False, idempotent_hint=True))
    async def update_ticket(
        ctx: Context,
        ticket_id: Annotated[TicketId, Field(description="Ticket id, e.g. 'INC-1042'.")],
        changes: TicketChanges,
    ) -> WriteOutcome:
        """Change status, priority, assignment or notes on a ticket."""
        async with tool_call(ctx, "update_ticket") as service:
            return await service.update_ticket(ticket_id, changes)

    return mcp


@asynccontextmanager
async def tool_call(ctx: Context, tool: str) -> AsyncIterator[TicketingService]:
    """Per-call plumbing: trace binding, timing/logging and error mapping."""
    bind_trace_id(_incoming_trace_id(ctx))
    started = time.perf_counter()
    outcome = "ok"
    try:
        yield ctx.request_context.lifespan_context
    except TicketingApiError as exc:
        outcome = type(exc).__name__
        raise ToolError(str(exc)) from exc
    finally:
        fields = {"tool": tool, "outcome": outcome, "duration_ms": round((time.perf_counter() - started) * 1000, 1)}
        logger.info("tool_call", extra={"fields": fields})


def _incoming_trace_id(ctx: Context) -> str | None:
    """Reuse the caller's trace id from request `_meta` when present."""
    meta = ctx.request_context.meta or {}
    return meta.get("trace_id")
