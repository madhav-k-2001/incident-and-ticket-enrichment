"""End-to-end MCP protocol tests: real MCP client <-> server in memory, fake Alarm API behind it.

The client is opened inside each test (not a yield fixture) because anyio task
groups must be entered and exited in the same task.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp.client import Client

from alarm_mcp.config import Settings
from alarm_mcp.server import create_server
from tests.conftest import FakeAlarmApi

EXPECTED_TOOLS = {"search_assets", "list_alarms", "get_alarm_context", "analyze_alarms", "find_correlated_alarms"}


@pytest.fixture
def connect(settings: Settings, fake_api: FakeAlarmApi):
    @asynccontextmanager
    async def _connect() -> AsyncIterator[Client]:
        server = create_server(settings, transport=httpx.MockTransport(fake_api))
        async with Client(server) as client:
            yield client

    return _connect


async def test_exposes_only_curated_read_only_tools(connect) -> None:
    async with connect() as client:
        tools = (await client.list_tools()).tools

    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool.description
        assert tool.output_schema is not None
        assert tool.annotations is not None and tool.annotations.read_only_hint is True


async def test_get_alarm_context_returns_structured_enrichment(connect) -> None:
    async with connect() as client:
        result = await client.call_tool("get_alarm_context", {"alarm_id": "ALM-1"})

    assert not result.is_error
    context = result.structured_content
    assert context["alarm"]["alarm_id"] == "ALM-1"
    assert context["asset"]["asset_id"] == "CMP-1"
    assert context["priority"]["recommended_priority_level"] == "P1"
    assert context["recommendations"]["likely_causes"] == ["Anti-surge valve stuck"]
    assert context["warnings"] == []


async def test_list_alarms_forwards_filters(connect, fake_api: FakeAlarmApi) -> None:
    async with connect() as client:
        result = await client.call_tool(
            "list_alarms", {"status": "active", "sort_by": "priority_score", "page_size": 5}
        )

    assert not result.is_error
    assert result.structured_content["alarms"][0]["alarm_id"] == "ALM-1"
    params = fake_api.requests[-1].url.params
    assert params["status"] == "active"
    assert params["sort_by"] == "priority_score"
    assert params["page_size"] == "5"


async def test_upstream_not_found_becomes_tool_error(connect) -> None:
    async with connect() as client:
        result = await client.call_tool("get_alarm_context", {"alarm_id": "ALM-404"})

    assert result.is_error
    message = result.content[0].text
    assert "trace_id=" in message
    assert "secret-token" not in message


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_alarm_context", {"alarm_id": "ALM-1; DROP TABLE"}),
        ("list_alarms", {"page_size": 1000}),
        ("list_alarms", {"severity": "catastrophic"}),
    ],
)
async def test_invalid_input_is_rejected_before_calling_api(
    connect, fake_api: FakeAlarmApi, tool: str, arguments: dict
) -> None:
    async with connect() as client:
        result = await client.call_tool(tool, arguments)

    assert result.is_error
    assert fake_api.requests == []
