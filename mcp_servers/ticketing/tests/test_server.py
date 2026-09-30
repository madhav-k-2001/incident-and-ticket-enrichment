"""End-to-end MCP protocol tests: real MCP client <-> server in memory, fake Ticketing API behind it.

The client is opened inside each test (not a yield fixture) because anyio task
groups must be entered and exited in the same task.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp.client import Client

from ticketing_mcp.config import Settings
from ticketing_mcp.server import create_server
from tests.conftest import FakeTicketingApi

EXPECTED_TOOLS = {"find_tickets", "search_similar_tickets", "create_ticket", "update_ticket"}

DRAFT = {
    "title": "Compressor C-201 discharge overpressure",
    "asset_id": "CMP-201",
    "alarm_id": "ALM-7102",
    "severity": "critical",
    "priority": "P1",
}


@pytest.fixture
def connect(settings: Settings, fake_api: FakeTicketingApi):
    @asynccontextmanager
    async def _connect() -> AsyncIterator[Client]:
        server = create_server(settings, transport=httpx.MockTransport(fake_api))
        async with Client(server) as client:
            yield client

    return _connect


async def test_exposes_only_the_ticketing_tools_with_schemas(connect) -> None:
    async with connect() as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert set(tools) == EXPECTED_TOOLS
    assert all(tool.description and tool.input_schema and tool.output_schema for tool in tools.values())
    assert tools["find_tickets"].annotations.read_only_hint is True
    assert tools["create_ticket"].annotations.read_only_hint is False


async def test_find_ticket_by_id(connect) -> None:
    async with connect() as client:
        result = await client.call_tool("find_tickets", {"ticket_id": "INC-1042"})

    assert not result.is_error
    assert [t["ticket_id"] for t in result.structured_content["tickets"]] == ["INC-1042"]


async def test_find_tickets_forwards_filters(connect, fake_api: FakeTicketingApi) -> None:
    async with connect() as client:
        result = await client.call_tool("find_tickets", {"asset_ids": ["CMP-201", "M-501"], "status": "open"})

    assert not result.is_error
    params = fake_api.requests[-1].url.params
    assert params["asset_ids"] == "CMP-201,M-501"
    assert params["status"] == "open"


async def test_search_similar_tickets_keeps_relevance_order(connect) -> None:
    async with connect() as client:
        result = await client.call_tool("search_similar_tickets", {"query": "discharge pressure", "limit": 3})

    tickets = result.structured_content["tickets"]
    assert tickets[0]["ticket_id"] == "INC-1042"
    scores = [t["relevance_score"] for t in tickets]
    assert scores == sorted(scores, reverse=True) and len(tickets) <= 3


async def test_create_ticket_without_confirm_is_preview_only(connect, fake_api: FakeTicketingApi) -> None:
    async with connect() as client:
        result = await client.call_tool("create_ticket", {"draft": DRAFT})

    assert result.structured_content["committed"] is False
    assert result.structured_content["pending_changes"]["asset_id"] == "CMP-201"
    assert fake_api.requests == []


async def test_create_ticket_with_confirm_writes_and_propagates_trace_id(connect, fake_api: FakeTicketingApi) -> None:
    async with connect() as client:
        result = await client.call_tool(
            "create_ticket", {"draft": DRAFT, "confirm": True}, meta={"trace_id": "trace-e2e-1"}
        )

    outcome = result.structured_content
    assert outcome["committed"] is True
    assert outcome["trace_id"] == "trace-e2e-1"
    assert fake_api.requests[0].headers["trace-id"] == "trace-e2e-1"
    assert outcome["ticket"]["audit_trail"][0]["trace_id"] == "trace-e2e-1"


async def test_update_ticket_preview_then_commit(connect) -> None:
    args = {"ticket_id": "INC-1188", "changes": {"status": "resolved", "work_notes": "Bearing replaced"}}

    async with connect() as client:
        preview = (await client.call_tool("update_ticket", args)).structured_content
        committed = (await client.call_tool("update_ticket", {**args, "confirm": True})).structured_content

    assert preview["committed"] is False and preview["ticket"]["status"] == "open"
    assert committed["ticket"]["status"] == "resolved"
    assert committed["ticket"]["resolved_at"] is not None


async def test_unknown_ticket_is_a_readable_tool_error(connect) -> None:
    async with connect() as client:
        result = await client.call_tool("find_tickets", {"ticket_id": "INC-0000"})

    assert result.is_error
    message = result.content[0].text
    assert "INC-0000 not found" in message
    assert "secret-token" not in message


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("find_tickets", {"ticket_id": "../assets/CMP-201/metadata"}),
        ("find_tickets", {"severity": "catastrophic"}),
        ("search_similar_tickets", {"query": "x"}),
        ("create_ticket", {"draft": {**DRAFT, "priority": "P9"}}),
        ("update_ticket", {"ticket_id": "INC-1188", "changes": {}}),
    ],
)
async def test_invalid_input_is_rejected_before_calling_api(
    connect, fake_api: FakeTicketingApi, tool: str, arguments: dict
) -> None:
    async with connect() as client:
        result = await client.call_tool(tool, arguments)

    assert result.is_error
    assert fake_api.requests == []
