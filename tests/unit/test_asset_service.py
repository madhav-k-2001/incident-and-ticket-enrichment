"""Unit tests for AssetService: Ambiguity, exact matching, token retries, and resolution."""

from __future__ import annotations

import httpx
import pytest

from mcp_servers.client import BaseSimulatorClient
from mcp_servers.config import SimulatorConfig
from mcp_servers.services.asset_service import AssetService


@pytest.fixture
def mock_asset_data():
    return [
        {
            "asset_id": "CMP-201",
            "asset_name": "Wet Gas Compressor 201",
            "site": "EastRefinery",
            "unit": "Unit 2",
            "type": "compressor",
            "criticality": "critical",
            "specifications": {"rated_power_kw": 4500},
        },
        {
            "asset_id": "CMP-202",
            "asset_name": "Wet Gas Compressor 202 (Standby)",
            "site": "EastRefinery",
            "unit": "Unit 2",
            "type": "compressor",
            "criticality": "high",
            "specifications": {"rated_power_kw": 4500},
        },
    ]


@pytest.mark.asyncio
async def test_resolve_asset_exact_id_match(mock_asset_data):
    """Amendment 4: Exact case-insensitive match on asset_id => resolved."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/assets/search":
            return httpx.Response(200, json={"results": mock_asset_data, "total": 2})
        if request.url.path == "/assets/CMP-201/metadata":
            return httpx.Response(200, json=mock_asset_data[0])
        return httpx.Response(404)

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    service = AssetService(client)

    result = await service.resolve_asset(query="cmp-201")
    assert result.status == "resolved"
    assert result.matched_asset is not None
    assert result.matched_asset.asset_id == "CMP-201"
    assert result.trace_id.startswith("trace-")


@pytest.mark.asyncio
async def test_resolve_asset_ambiguous_matches(mock_asset_data):
    """Amendment 4: Multiple non-exact matches => ambiguous with candidate summaries."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/assets/search":
            return httpx.Response(200, json={"results": mock_asset_data, "total": 2})
        return httpx.Response(404)

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    service = AssetService(client)

    result = await service.resolve_asset(query="Compressor")
    assert result.status == "ambiguous"
    assert result.matched_asset is None
    assert len(result.candidate_assets) == 2
    assert result.candidate_assets[0].asset_id == "CMP-201"
    assert result.candidate_assets[1].asset_id == "CMP-202"


@pytest.mark.asyncio
async def test_resolve_asset_single_result(mock_asset_data):
    """Amendment 4: Single match => resolved."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/assets/search":
            return httpx.Response(200, json={"results": [mock_asset_data[0]], "total": 1})
        if request.url.path == "/assets/CMP-201/metadata":
            return httpx.Response(200, json=mock_asset_data[0])
        return httpx.Response(404)

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    service = AssetService(client)

    result = await service.resolve_asset(query="201")
    assert result.status == "resolved"
    assert result.matched_asset.asset_id == "CMP-201"


@pytest.mark.asyncio
async def test_resolve_asset_token_retry(mock_asset_data):
    """Amendment 4: Zero matches on full text => retries with normalized tokens."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params.get("query", "")
        calls.append(q)
        if q == "Wet Gas Compressor 201 broken":
            return httpx.Response(200, json={"results": [], "total": 0})
        if q == "Wet":
            return httpx.Response(200, json={"results": [mock_asset_data[0]], "total": 1})
        if request.url.path == "/assets/CMP-201/metadata":
            return httpx.Response(200, json=mock_asset_data[0])
        return httpx.Response(200, json={"results": [], "total": 0})

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    service = AssetService(client)

    result = await service.resolve_asset(query="Wet Gas Compressor 201 broken")
    assert result.status == "resolved"
    assert result.matched_asset.asset_id == "CMP-201"
    assert "Wet" in calls


@pytest.mark.asyncio
async def test_resolve_asset_optional_query(mock_asset_data):
    """Amendment 4: Query is optional (search by unit/site alone)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/assets/search":
            assert "query" not in request.url.params
            assert request.url.params.get("unit") == "Unit 2"
            return httpx.Response(200, json={"results": [mock_asset_data[0]], "total": 1})
        if request.url.path == "/assets/CMP-201/metadata":
            return httpx.Response(200, json=mock_asset_data[0])
        return httpx.Response(404)

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    service = AssetService(client)

    result = await service.resolve_asset(unit="Unit 2")
    assert result.status == "resolved"
    assert result.matched_asset.asset_id == "CMP-201"


@pytest.mark.asyncio
async def test_resolve_asset_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [], "total": 0})

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    service = AssetService(client)

    result = await service.resolve_asset(query="nonexistent-xyz")
    assert result.status == "not_found"
    assert result.matched_asset is None
