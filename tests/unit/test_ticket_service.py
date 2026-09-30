"""Unit tests for TicketService: Keyword Search, Idempotency, Status/Severity Filtering, and Updates."""

from __future__ import annotations

import httpx
import pytest

from mcp_servers.client import BaseSimulatorClient
from mcp_servers.config import SimulatorConfig
from mcp_servers.schemas.tickets import TicketCreateRequest, TicketUpdateRequest
from mcp_servers.services.ticket_service import TicketService


@pytest.fixture
def mock_tickets():
    return [
        {
            "ticket_id": "INC-1042",
            "asset_id": "CMP-201",
            "alarm_id": "ALM-7102",
            "title": "Wet Gas Compressor 201 High Discharge Pressure Trip",
            "status": "closed",
            "severity": "critical",
            "priority": "P1",
            "symptom": "Discharge pressure spiked past 46 bar",
            "root_cause": "Anti-surge valve positioner feedback linkage mechanically loose",
            "resolution_notes": "Tightened feedback arm linkage on FV-201",
            "assigned_to": "Instrumentation Team",
            "assigned_user": "Sarah Jenkins",
            "created_at": "2026-03-14T09:20:00Z",
            "resolved_at": "2026-03-14T14:45:00Z",
        },
        {
            "ticket_id": "INC-1188",
            "asset_id": "M-501",
            "alarm_id": "ALM-9022",
            "title": "Drive Motor 501 Inboard Bearing Vibration Warning",
            "status": "open",
            "severity": "medium",
            "priority": "P3",
            "symptom": "Continuous vibration reading ~7.8 mm/s",
            "root_cause": "Pending phase analysis",
            "resolution_notes": "High-frequency acoustic sensors installed",
            "assigned_to": "Mechanical Team",
            "assigned_user": "Elena Rostova",
            "created_at": "2026-09-28T11:00:00Z",
            "resolved_at": None,
        },
    ]


@pytest.mark.asyncio
async def test_search_or_list_cleans_keywords_and_post_filters(mock_tickets):
    """
    Amendment 5:
    - Cleans stopwords out of long queries.
    - Searches without asset_id when multiple asset_ids passed, and post-filters by asset_ids.
    - Post-filters by status and severity.
    - Applies limit after post-filtering.
    """
    captured_query = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_query
        captured_query = request.url.params.get("query")
        # Simulator search returns both tickets
        return httpx.Response(200, json={"query": captured_query, "results": mock_tickets, "count": 2})

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    service = TicketService(client)

    # Query with stopwords and conversational noise
    result = await service.search_or_list_tickets(
        query="Please find me the prior tickets related to high discharge pressure problem",
        asset_ids=["CMP-201", "CMP-202"],  # Multiple asset_ids
        status="closed",
        severity="critical",
        limit=10,
    )

    # Verify stopwords were stripped
    assert "please" not in captured_query
    assert "the" not in captured_query
    assert "related" not in captured_query
    assert "high" in captured_query
    assert "discharge" in captured_query

    # Post-filtering checks
    assert result.total_count == 1
    assert result.matched_tickets[0].ticket_id == "INC-1042"
    assert result.matched_tickets[0].status == "closed"


@pytest.mark.asyncio
async def test_search_falls_back_to_list_when_only_stopwords(mock_tickets):
    """Amendment 5: If cleaning leaves no keywords, falls back to list mode."""
    called_path = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called_path
        called_path = request.url.path
        return httpx.Response(200, json={"tickets": mock_tickets, "total": 2})

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    service = TicketService(client)

    result = await service.search_or_list_tickets(query="the a is on to")
    assert called_path == "/tickets"  # List endpoint called, NOT /tickets/search!
    assert result.cleaned_keywords == []
    assert len(result.matched_tickets) == 2


@pytest.mark.asyncio
async def test_create_ticket_idempotency(mock_tickets):
    """
    Amendment 6:
    - Guarded with asyncio.Lock and TTL.
    - Caches successful creations.
    - Duplicate creation returns cached ticket with is_duplicate=True.
    """
    post_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_calls
        if request.method == "POST" and request.url.path == "/tickets":
            post_calls += 1
            return httpx.Response(201, json={
                "message": "Incident ticket created",
                "ticket": dict(mock_tickets[0], ticket_id=f"INC-{1000 + post_calls}"),
            })
        return httpx.Response(404)

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    service = TicketService(client, idempotency_ttl=10.0)

    payload = TicketCreateRequest(
        title="Compressor High Vibration",
        asset_id="CMP-201",
        alarm_id="ALM-9021",
        severity="high",
        priority="P2",
    )

    # Call 1: fresh create
    res1 = await service.create_incident_ticket(payload, idempotency_key="idemp-key-101")
    assert res1.is_duplicate is False
    assert res1.ticket.ticket_id == "INC-1001"
    assert post_calls == 1

    # Call 2: duplicate attempt with same key
    res2 = await service.create_incident_ticket(payload, idempotency_key="idemp-key-101")
    assert res2.is_duplicate is True
    assert res2.ticket.ticket_id == "INC-1001"
    assert post_calls == 1  # Network call was skipped!


@pytest.mark.asyncio
async def test_update_ticket(mock_tickets):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH" and request.url.path == "/tickets/INC-1042":
            return httpx.Response(200, json={
                "message": "Ticket INC-1042 updated",
                "ticket": dict(mock_tickets[0], status="resolved"),
            })
        return httpx.Response(404)

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    service = TicketService(client)

    update_res = await service.update_incident_ticket(
        "INC-1042",
        TicketUpdateRequest(status="resolved", work_notes="Replaced linkage arm"),
    )
    assert update_res.ticket.status == "resolved"
    assert "updated" in update_res.message
