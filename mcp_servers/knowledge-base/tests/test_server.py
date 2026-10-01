"""End-to-end MCP protocol tests: real MCP client <-> server in memory, fake repository behind it.

The client is opened inside each test (not a yield fixture) because anyio task
groups must be entered and exited in the same task.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from mcp.client import Client
from mcp.shared.exceptions import MCPError

from kb_mcp.config import Settings
from kb_mcp.server import create_server
from tests.conftest import SOP_ID, TSG_ID, FakeRepository, FixedEmbedder

EXPECTED_TOOLS = {
    "search_knowledge_base",
    "list_documents",
    "read_document",
    "get_chunk_context",
    "get_knowledge_base_status",
}


@pytest.fixture
def connect(settings: Settings, repo: FakeRepository, embedder: FixedEmbedder):
    @asynccontextmanager
    async def _connect() -> AsyncIterator[Client]:
        server = create_server(settings, repository=repo, embedder=embedder)
        async with Client(server) as client:
            yield client

    return _connect


async def test_exposes_read_only_tools_with_schemas(connect) -> None:
    async with connect() as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert set(tools) == EXPECTED_TOOLS
    assert all(tool.description and tool.input_schema and tool.output_schema for tool in tools.values())
    assert all(tool.annotations.read_only_hint is True for tool in tools.values())


async def test_lists_catalog_and_one_resource_per_ingested_document(connect) -> None:
    async with connect() as client:
        resources = {str(r.uri): r for r in (await client.list_resources()).resources}
        templates = {t.uri_template for t in (await client.list_resource_templates()).resource_templates}

    assert set(resources) == {"kb://documents", f"kb://documents/{SOP_ID}", f"kb://documents/{TSG_ID}"}
    assert resources[f"kb://documents/{SOP_ID}"].name == "SOP-CMP-201-Discharge-Overpressure.pdf"
    assert resources[f"kb://documents/{SOP_ID}"].mime_type == "text/markdown"
    assert templates == {"kb://documents/{document_id}", "kb://documents/{document_id}/chunks/{chunk_index}"}


async def test_resource_listing_survives_a_database_outage(connect, repo: FakeRepository) -> None:
    repo.unavailable = True
    async with connect() as client:
        resources = [str(r.uri) for r in (await client.list_resources()).resources]

    assert resources == ["kb://documents"]


async def test_read_document_resource(connect) -> None:
    async with connect() as client:
        result = await client.read_resource(f"kb://documents/{SOP_ID}")

    content = result.contents[0]
    assert content.mime_type == "text/markdown"
    assert content.text.startswith("# SOP-CMP-201-Discharge-Overpressure.pdf")
    assert content.text.count("pressure alarm on CMP-201") == 1


async def test_read_chunk_and_catalog_resources(connect) -> None:
    async with connect() as client:
        chunk = await client.read_resource(f"kb://documents/{TSG_ID}/chunks/1")
        catalog = await client.read_resource("kb://documents")

    assert chunk.contents[0].text == "Check the positioner air supply and stroke the valve."
    data = json.loads(catalog.contents[0].text)
    assert data["total"] == 3
    assert {d["resource_uri"] for d in data["documents"]} >= {f"kb://documents/{SOP_ID}"}


@pytest.mark.parametrize(
    "uri",
    ["kb://documents/does-not-exist", f"kb://documents/{SOP_ID}/chunks/99", f"kb://documents/{SOP_ID}/chunks/x"],
)
async def test_unknown_resources_are_errors(connect, uri: str) -> None:
    async with connect() as client:
        with pytest.raises(MCPError):
            await client.read_resource(uri)


async def test_search_returns_hits_with_resource_uris_and_trace_id(connect) -> None:
    async with connect() as client:
        result = await client.call_tool(
            "search_knowledge_base", {"query": "anti-surge valve stuck", "top_k": 2}, meta={"trace_id": "trace-kb-1"}
        )

    assert not result.is_error
    data = result.structured_content
    assert data["trace_id"] == "trace-kb-1"
    assert data["mode"] == "hybrid" and len(data["hits"]) == 2
    assert data["hits"][0]["resource_uri"] == f"kb://documents/{TSG_ID}/chunks/0"


async def test_search_forwards_document_filter(connect, repo: FakeRepository) -> None:
    async with connect() as client:
        result = await client.call_tool(
            "search_knowledge_base", {"query": "valve", "mode": "semantic", "document_ids": [SOP_ID]}
        )

    assert {h["document_id"] for h in result.structured_content["hits"]} == {SOP_ID}
    assert repo.calls[-1][1]["document_ids"] == [SOP_ID]


async def test_list_documents_and_read_document(connect) -> None:
    async with connect() as client:
        listed = (await client.call_tool("list_documents", {"status": "COMPLETED"})).structured_content
        passage = (await client.call_tool("read_document", {"document_id": SOP_ID, "max_chunks": 2})).structured_content

    assert listed["total"] == 2
    assert passage["next_chunk"] == 2 and len(passage["chunks"]) == 2


async def test_chunk_context_and_status(connect) -> None:
    async with connect() as client:
        context = await client.call_tool("get_chunk_context", {"document_id": SOP_ID, "chunk_index": 2})
        status = await client.call_tool("get_knowledge_base_status", {})

    assert [c["chunk_index"] for c in context.structured_content["chunks"]] == [1, 2]
    assert status.structured_content["total_chunks"] == 5


async def test_unknown_document_is_a_readable_tool_error(connect) -> None:
    async with connect() as client:
        result = await client.call_tool("read_document", {"document_id": "deadbeef"})

    assert result.is_error
    assert "deadbeef not found" in result.content[0].text


async def test_database_outage_is_a_readable_tool_error(connect, repo: FakeRepository) -> None:
    repo.unavailable = True
    async with connect() as client:
        result = await client.call_tool("list_documents", {})

    assert result.is_error
    assert "unreachable" in result.content[0].text


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("search_knowledge_base", {"query": "x"}),
        ("search_knowledge_base", {"query": "valve", "mode": "fuzzy"}),
        ("search_knowledge_base", {"query": "valve", "top_k": 500}),
        ("search_knowledge_base", {"query": "valve", "document_ids": ["../etc/passwd"]}),
        ("read_document", {"document_id": "'; DROP TABLE documents; --"}),
        ("get_chunk_context", {"document_id": SOP_ID, "chunk_index": -1}),
        ("list_documents", {"status": "DONE"}),
    ],
)
async def test_invalid_input_is_rejected_before_querying(
    connect, repo: FakeRepository, tool: str, arguments: dict
) -> None:
    async with connect() as client:
        result = await client.call_tool(tool, arguments)

    assert result.is_error
    assert repo.calls == []
