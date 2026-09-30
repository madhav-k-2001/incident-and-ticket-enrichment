"""Unit tests for ticket_mcp_server tool registration and execution."""

from __future__ import annotations

import httpx
import pytest

from mcp_servers.client import BaseSimulatorClient
from mcp_servers.config import SimulatorConfig
from mcp_servers.ticket_mcp_server import create_ticket_server


@pytest.fixture
def mock_tickets_data():
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
            "resolution_notes": "Tightened feedback arm linkage on FV-201 to 8.5 Nm torque",
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


@pytest.fixture
def ticket_mcp_server(mock_tickets_data):
    def handler(request: httpx.Request) -> httpx.Response:
        url_path = request.url.path
        method = request.method

        if method == "GET" and url_path == "/tickets/search":
            q = request.url.params.get("query", "")
            results = [
                t for t in mock_tickets_data
                if any(k in t["title"].lower() or k in (t.get("symptom") or "").lower() for k in q.split())
            ] or mock_tickets_data
            return httpx.Response(200, json={"query": q, "results": results, "count": len(results)})

        if method == "GET" and url_path == "/tickets":
            return httpx.Response(200, json={"tickets": mock_tickets_data, "total": len(mock_tickets_data)})

        if method == "GET" and url_path.startswith("/tickets/INC-"):
            tid = url_path.split("/")[-1]
            match = next((t for t in mock_tickets_data if t["ticket_id"] == tid), None)
            if match:
                return httpx.Response(200, json=match)
            return httpx.Response(404, json={"detail": f"Ticket {tid} not found"})

        if method == "POST" and url_path == "/tickets":
            data = request.read()
            import json
            body = json.loads(data)
            created_ticket = {
                "ticket_id": "INC-2026",
                "asset_id": body.get("asset_id", "CMP-201"),
                "alarm_id": body.get("alarm_id"),
                "title": body.get("title", "New Incident"),
                "status": "open",
                "severity": body.get("severity", "high"),
                "priority": body.get("priority", "P2"),
                "symptom": body.get("symptom", ""),
                "likely_cause": body.get("likely_cause", ""),
                "recommended_action": body.get("recommended_action", ""),
                "sop_reference": body.get("sop_reference", ""),
                "assigned_to": body.get("assigned_to", "Instrumentation Team"),
                "assigned_user": body.get("assigned_user", "Unassigned"),
                "created_at": "2026-09-30T10:00:00Z",
            }
            return httpx.Response(201, json={"ticket": created_ticket, "message": "Ticket created"})

        if method == "PATCH" and url_path.startswith("/tickets/INC-"):
            tid = url_path.split("/")[-1]
            import json
            body = json.loads(request.read())
            updated = dict(mock_tickets_data[0])
            updated["ticket_id"] = tid
            for k, v in body.items():
                if v is not None:
                    updated[k] = v
            return httpx.Response(200, json={"ticket": updated, "message": "Ticket updated"})

        return httpx.Response(404, json={"detail": "Not found"})

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    return create_ticket_server(client=client)


@pytest.mark.asyncio
async def test_ticket_server_tool_registration(ticket_mcp_server):
    """Verify ticket_mcp_server exposes exactly 4 tools with valid schemas."""
    tools = await ticket_mcp_server.list_tools()
    tool_names = {t.name for t in tools}

    assert len(tools) == 4
    expected_tools = {
        "search_or_list_tickets",
        "get_ticket",
        "create_incident_ticket",
        "update_incident_ticket",
    }
    assert tool_names == expected_tools


@pytest.mark.asyncio
async def test_search_or_list_tickets_execution(ticket_mcp_server):
    """Test searching tickets with keyword cleaning and multi-asset filtering."""
    result = await ticket_mcp_server.call_tool(
        "search_or_list_tickets",
        {
            "query": "find prior tickets related to compressor linkage",
            "asset_ids": ["CMP-201"],
            "limit": 5,
        },
    )
    assert not result.is_error
    import json
    data = json.loads(result.content[0].text)
    assert data["total_count"] >= 1
    assert "linkage" in data["cleaned_keywords"]
    assert data["matched_tickets"][0]["ticket_id"] == "INC-1042"


@pytest.mark.asyncio
async def test_get_ticket_execution(ticket_mcp_server):
    """Test retrieving full ticket details by ID."""
    result = await ticket_mcp_server.call_tool("get_ticket", {"ticket_id": "INC-1042"})
    assert not result.is_error
    import json
    data = json.loads(result.content[0].text)
    assert data["ticket_id"] == "INC-1042"
    assert data["asset_id"] == "CMP-201"
    assert "Tightened feedback arm linkage" in data["resolution_notes"]


@pytest.mark.asyncio
async def test_create_incident_ticket_idempotency(ticket_mcp_server):
    """Test creating a new incident ticket and verify idempotency protection."""
    payload = {
        "title": "Wet Gas Compressor 201 Valve Stem Binding",
        "asset_id": "CMP-201",
        "alarm_id": "ALM-7104",
        "severity": "critical",
        "priority": "P1",
        "symptom": "Discharge pressure 48.5 bar, valve stuck at 15%",
        "likely_cause": "Actuator positioner linkage loose",
        "sop_reference": "SOP-CMP-201 §2.1",
        "idempotency_key": "custom-idemp-key-99",
    }

    # First call: creates ticket
    res1 = await ticket_mcp_server.call_tool("create_incident_ticket", payload)
    assert not res1.is_error
    import json
    data1 = json.loads(res1.content[0].text)
    assert data1["ticket"]["ticket_id"] == "INC-2026"
    assert data1["is_duplicate"] is False

    # Second call with same idempotency key: duplicate refused
    res2 = await ticket_mcp_server.call_tool("create_incident_ticket", payload)
    assert not res2.is_error
    data2 = json.loads(res2.content[0].text)
    assert data2["is_duplicate"] is True
    assert "Duplicate ticket creation refused" in data2["message"]


@pytest.mark.asyncio
async def test_update_incident_ticket_execution(ticket_mcp_server):
    """Test updating existing ticket status and work notes."""
    result = await ticket_mcp_server.call_tool(
        "update_incident_ticket",
        {
            "ticket_id": "INC-1042",
            "status": "in_progress",
            "work_notes": "Technician dispatched to Compressor Hall 2 with torque wrench.",
        },
    )
    assert not result.is_error
    import json
    data = json.loads(result.content[0].text)
    assert data["ticket"]["ticket_id"] == "INC-1042"
    assert data["ticket"]["status"] == "in_progress"
