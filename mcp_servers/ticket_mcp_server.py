"""
Ticket MCP Server.

Exposes 4 operational tools for incident ticket discovery, detailed ticket retrieval,
idempotent ticket creation (HITL), and ticket status/work notes updates (HITL).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import List, Optional

# FastMCP / MCPServer dual-compatibility layer
try:
    from mcp.server.fastmcp import FastMCP
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as FastMCP

from mcp_servers.client import BaseSimulatorClient
from mcp_servers.config import SimulatorConfig
from mcp_servers.schemas.common import Severity, TicketPriority, TicketStatus
from mcp_servers.schemas.tickets import (
    Ticket,
    TicketCreateRequest,
    TicketCreateResult,
    TicketSearchResult,
    TicketUpdateRequest,
    TicketUpdateResult,
)
from mcp_servers.services.ticket_service import TicketService

logger = logging.getLogger("mcp_servers.ticket_server")


def create_ticket_server(
    client: Optional[BaseSimulatorClient] = None,
    config: Optional[SimulatorConfig] = None,
    idempotency_ttl: float = 300.0,
) -> FastMCP:
    """
    Factory creating and configuring the ticket_mcp_server with all 4 tools.
    Allows injecting mock clients or custom idempotency TTL for automated testing.
    """
    sim_client = client or BaseSimulatorClient(config=config)
    ticket_service = TicketService(client=sim_client, idempotency_ttl=idempotency_ttl)

    mcp = FastMCP(
        name="ticket_mcp_server",
        instructions=(
            "East Refinery Plant Ticketing and Incident Management MCP Server. "
            "Provides tools for multi-asset keyword ticket search, full ticket details lookup, "
            "idempotent incident ticket creation with operator confirmation (Human-in-the-Loop), "
            "and ticket lifecycle updates."
        ),
    )

    # --------------------------------------------------------------------------
    # Tool 1: search_or_list_tickets
    # --------------------------------------------------------------------------
    @mcp.tool(
        name="search_or_list_tickets",
        description=(
            "Search or list support and incident tickets across plant equipment. "
            "Cleans search keywords (stripping domain stopwords) and queries the backend. "
            "Supports filtering by single or multiple asset IDs, ticket status (open, in_progress, resolved, closed), "
            "and severity (critical, high, medium, low). Applies limit after multi-asset post-filtering."
        ),
    )
    async def search_or_list_tickets(
        query: Optional[str] = None,
        asset_ids: Optional[List[str]] = None,
        status: Optional[TicketStatus] = None,
        severity: Optional[Severity] = None,
        limit: int = 20,
    ) -> TicketSearchResult:
        """Search similar tickets by keywords or list tickets filtered by assets and status."""
        return await ticket_service.search_or_list_tickets(
            query=query,
            asset_ids=asset_ids,
            status=status,
            severity=severity,
            limit=limit,
        )

    # --------------------------------------------------------------------------
    # Tool 2: get_ticket
    # --------------------------------------------------------------------------
    @mcp.tool(
        name="get_ticket",
        description=(
            "Retrieve complete details for a single incident ticket by its unique identifier (e.g. 'INC-1042'). "
            "Returns ticket title, asset, alarm linkage, status, severity, priority, symptom, "
            "likely cause, resolution notes, and audit history."
        ),
    )
    async def get_ticket(
        ticket_id: str,
    ) -> Ticket:
        """Retrieve full details of a specific ticket by ticket ID."""
        return await ticket_service.get_ticket(ticket_id=ticket_id)

    # --------------------------------------------------------------------------
    # Tool 3: create_incident_ticket
    # --------------------------------------------------------------------------
    @mcp.tool(
        name="create_incident_ticket",
        description=(
            "Create a new incident ticket in the system of record with idempotency protection. "
            "Prevents duplicate ticket generation within a rolling TTL using an idempotency key or content fingerprint. "
            "NOTE: This is a mutating write operation that requires explicit operator approval (Human-in-the-Loop)."
        ),
    )
    async def create_incident_ticket(
        title: str,
        asset_id: str,
        alarm_id: Optional[str] = None,
        severity: Optional[Severity] = "high",
        priority: Optional[TicketPriority] = "P2",
        symptom: Optional[str] = "",
        likely_cause: Optional[str] = "",
        recommended_action: Optional[str] = "",
        sop_reference: Optional[str] = "",
        assigned_to: Optional[str] = "Instrumentation Team",
        assigned_user: Optional[str] = "Unassigned",
        similar_ticket_ref: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> TicketCreateResult:
        """Create a new incident ticket with idempotency guard."""
        req = TicketCreateRequest(
            title=title,
            asset_id=asset_id,
            alarm_id=alarm_id,
            severity=severity or "high",
            priority=priority or "P2",
            symptom=symptom or "",
            likely_cause=likely_cause or "",
            recommended_action=recommended_action or "",
            sop_reference=sop_reference or "",
            assigned_to=assigned_to or "Instrumentation Team",
            assigned_user=assigned_user or "Unassigned",
            similar_ticket_ref=similar_ticket_ref,
        )
        return await ticket_service.create_incident_ticket(
            request=req,
            idempotency_key=idempotency_key,
        )

    # --------------------------------------------------------------------------
    # Tool 4: update_incident_ticket
    # --------------------------------------------------------------------------
    @mcp.tool(
        name="update_incident_ticket",
        description=(
            "Update an existing incident ticket's lifecycle status, priority, assignment, or work/resolution notes. "
            "NOTE: This is a mutating write operation that requires operator confirmation (Human-in-the-Loop)."
        ),
    )
    async def update_incident_ticket(
        ticket_id: str,
        status: Optional[TicketStatus] = None,
        priority: Optional[TicketPriority] = None,
        assigned_to: Optional[str] = None,
        assigned_user: Optional[str] = None,
        resolution_notes: Optional[str] = None,
        work_notes: Optional[str] = None,
    ) -> TicketUpdateResult:
        """Update ticket status, priority, assignment, or notes."""
        req = TicketUpdateRequest(
            status=status,
            priority=priority,
            assigned_to=assigned_to,
            assigned_user=assigned_user,
            resolution_notes=resolution_notes,
            work_notes=work_notes,
        )
        return await ticket_service.update_incident_ticket(
            ticket_id=ticket_id,
            request=req,
        )

    return mcp


# Module-level instance for direct import or fastmcp CLI
mcp = create_ticket_server()
server = mcp


def main() -> None:
    """CLI entrypoint for running ticket_mcp_server."""
    parser = argparse.ArgumentParser(description="Plant Ticket MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="MCP transport to run (default: stdio)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host for SSE / HTTP transport")
    parser.add_argument("--port", type=int, default=8002, help="Port for SSE / HTTP transport (default: 8002)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    logger.info("Starting ticket_mcp_server on transport=%s port=%d", args.transport, args.port)

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    elif args.transport == "sse":
        asyncio.run(mcp.run_sse_async(host=args.host, port=args.port))
    elif args.transport == "streamable-http":
        asyncio.run(mcp.run_streamable_http_async(host=args.host, port=args.port))


if __name__ == "__main__":
    main()
