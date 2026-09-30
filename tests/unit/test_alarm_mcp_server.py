"""Unit tests for alarm_mcp_server tool registration and execution."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock
import httpx
import pytest

from mcp_servers.alarm_mcp_server import create_alarm_server
from mcp_servers.client import BaseSimulatorClient
from mcp_servers.config import SimulatorConfig
from mcp_servers.schemas.documents import (
    ChunkContextResponse,
    ChunkRetrievalResult,
    DocumentRetrievalResponse,
)


@pytest.fixture
def mock_simulator_responses():
    """Mock dataset mirroring exact plant simulator endpoints."""
    return {
        "asset": {
            "asset_id": "CMP-201",
            "asset_name": "Wet Gas Compressor 201",
            "unit": "Unit 2",
            "site": "EastRefinery",
            "type": "Centrifugal Compressor",
            "criticality": "critical",
            "specifications": {
                "design_pressure_bar": 52.0,
                "operating_temp_c": 110.0,
                "driver": "M-501",
            },
        },
        "alarm": {
            "alarm_id": "ALM-7104",
            "asset_id": "CMP-201",
            "asset_name": "Wet Gas Compressor 201",
            "site": "EastRefinery",
            "unit": "Unit 2",
            "alarm_code": "CMP-DISCH-P-HI",
            "alarm_name": "2nd Stage High Discharge Pressure",
            "severity": "critical",
            "status": "active",
            "start_time": "2026-09-30T09:00:00Z",
            "end_time": None,
            "ack_time": None,
            "value": 48.5,
            "threshold": 42.0,
            "unit_of_measure": "bar",
            "priority_score": 95.0,
        },
        "priority_score": {
            "alarm_id": "ALM-7104",
            "priority_score": 95.0,
            "severity": "critical",
            "urgency": "immediate",
            "recommended_priority_level": "P1",
            "factors": {
                "asset_criticality_weight": 1.5,
                "safety_trip_proximity": "high",
                "recurrence_penalty": 1.2,
            },
        },
        "recommendations": {
            "alarm_id": "ALM-7104",
            "asset_id": "CMP-201",
            "likely_causes": ["Anti-surge valve positioner linkage loose"],
            "immediate_actions": [
                "Switch anti-surge controller FIC-201 to MANUAL mode",
                "Stroke anti-surge recycle valve 02-FV-201 to 25% open",
            ],
            "safety_precautions": ["Dispatch outside operator with PPE to Compressor Hall 2"],
            "related_assets": [],
            "historical_insights": "Past incident INC-1042 showed identical linkage failure",
        },
        "correlation": {
            "correlations": [
                {
                    "source_asset": "CMP-201",
                    "target_asset": "M-501",
                    "source_alarm_code": "CMP-DISCH-P-HI",
                    "target_alarm_code": "MTR-VIB-HI",
                    "correlation_coefficient": 0.91,
                    "avg_lag_minutes": 3.5,
                    "cooccurrence_count": 5,
                    "significance": "high",
                    "description": "Compressor discharge overpressure consistently precedes Drive Motor vibration",
                }
            ],
            "correlation_method": "cooccurrence",
        },
        "flood": {
            "unit": "Unit 2",
            "threshold_count": 10,
            "rolling_window_minutes": 10,
            "flood_events_count": 1,
            "flood_windows": [
                {
                    "start": "2026-09-30T08:00:00Z",
                    "end": "2026-09-30T08:15:00Z",
                    "alarm_count": 18,
                    "peak_rate_per_min": 3.2,
                    "primary_contributing_assets": ["CMP-201", "M-501"],
                }
            ],
        },
        "rationalization": {
            "candidates": [
                {
                    "alarm_id": "ALM-6001",
                    "asset_id": "CMP-201",
                    "alarm_code": "CMP-DISCH-P-HI",
                    "alarm_name": "2nd Stage High Discharge Pressure",
                    "classification": "nuisance",
                    "recurrence_count": 12,
                    "stale_duration_minutes": 0,
                    "justification": "Rapid spikes without trip",
                    "recommended_action": "Tune deadband threshold from 0.5 to 1.2 bar",
                }
            ]
        },
    }


@pytest.fixture
def alarm_mcp_server(mock_simulator_responses):
    def handler(request: httpx.Request) -> httpx.Response:
        url_path = request.url.path
        method = request.method

        if method == "GET" and url_path == "/assets/search":
            return httpx.Response(
                200,
                json={"results": [mock_simulator_responses["asset"]], "total": 1},
            )

        if method == "GET" and url_path == "/assets/CMP-201/metadata":
            return httpx.Response(200, json=mock_simulator_responses["asset"])

        if method == "GET" and url_path == "/alarms/ALM-7104":
            return httpx.Response(200, json=mock_simulator_responses["alarm"])

        if method == "GET" and url_path == "/alarms":
            asset_param = request.url.params.get("asset_id")
            # If historical query for BFP-101
            if asset_param == "BFP-101":
                bfp_alarms = [
                    {
                        "alarm_id": f"ALM-BFP-{i}",
                        "asset_id": "BFP-101",
                        "alarm_code": "BFP-BEAR-TEMP-HI",
                        "alarm_name": "Thrust Bearing Temperature High",
                        "severity": "high",
                        "priority_score": 82.0,
                        "status": "cleared",
                        "start_time": f"2026-09-{10+i:02d}T10:00:00Z",
                    }
                    for i in range(5)
                ]
                return httpx.Response(
                    200,
                    json={
                        "data": bfp_alarms,
                        "pagination": {"page": 1, "page_size": 50, "total_pages": 1, "total_records": 5},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "data": [mock_simulator_responses["alarm"]],
                    "pagination": {"page": 1, "page_size": 50, "total_pages": 1, "total_records": 1},
                },
            )

        if method == "POST" and url_path == "/alarms/priority-score":
            return httpx.Response(200, json=mock_simulator_responses["priority_score"])

        if method == "POST" and url_path == "/recommendations/operator-actions":
            return httpx.Response(200, json=mock_simulator_responses["recommendations"])

        if method == "POST" and url_path == "/alarms/correlation":
            return httpx.Response(200, json=mock_simulator_responses["correlation"])

        if method == "POST" and url_path == "/alarms/flood-analysis":
            return httpx.Response(200, json=mock_simulator_responses["flood"])

        if method == "POST" and url_path == "/alarms/rationalization-candidates":
            return httpx.Response(200, json=mock_simulator_responses["rationalization"])

        return httpx.Response(404, json={"detail": "Not found"})

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    rag_corpus_path = Path(__file__).resolve().parent.parent.parent / "test_data" / "rag_data"

    # Inject mock doc_service to avoid OS-level TCP socket connection timeout when Postgres is offline
    mock_doc_service = AsyncMock()
    mock_doc_service.keyword_search.side_effect = ConnectionError("Postgres offline in unit tests")

    return create_alarm_server(client=client, doc_service=mock_doc_service, rag_dir=rag_corpus_path)


@pytest.mark.asyncio
async def test_alarm_server_tool_registration(alarm_mcp_server):
    """Verify alarm_mcp_server exposes exactly 7 tools with valid schemas."""
    tools = await alarm_mcp_server.list_tools()
    tool_names = {t.name for t in tools}

    assert len(tools) == 7
    expected_tools = {
        "resolve_asset",
        "get_alarms",
        "enrich_alarm_context",
        "get_alarm_history",
        "get_correlations",
        "analyze_alarm_hygiene",
        "search_knowledge_base",
    }
    assert tool_names == expected_tools


@pytest.mark.asyncio
async def test_resolve_asset_execution(alarm_mcp_server):
    """Test resolving asset by query string."""
    result = await alarm_mcp_server.call_tool("resolve_asset", {"query": "CMP-201"})
    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data["status"] == "resolved"
    assert data["matched_asset"]["asset_id"] == "CMP-201"
    assert data["matched_asset"]["asset_name"] == "Wet Gas Compressor 201"


@pytest.mark.asyncio
async def test_get_alarms_execution(alarm_mcp_server):
    """Test filtering alarms by severity and status."""
    result = await alarm_mcp_server.call_tool(
        "get_alarms",
        {"site": "EastRefinery", "status": "active", "severity": "critical", "limit": 10},
    )
    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data["total_count"] >= 1
    assert data["alarms"][0]["alarm_id"] == "ALM-7104"
    assert data["alarms"][0]["priority_score"] == 95.0


@pytest.mark.asyncio
async def test_enrich_alarm_context_execution(alarm_mcp_server):
    """Test full context enrichment for ALM-7104."""
    result = await alarm_mcp_server.call_tool("enrich_alarm_context", {"alarm_id": "ALM-7104"})
    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data["alarm"]["alarm_id"] == "ALM-7104"
    assert data["asset"]["asset_id"] == "CMP-201"
    assert data["priority_evaluation"]["priority_score"] == 95.0
    assert len(data["operator_guidance"]["immediate_actions"]) >= 1


@pytest.mark.asyncio
async def test_get_alarm_history_execution(alarm_mcp_server):
    """Test 90-day recurrence analysis for BFP-101."""
    result = await alarm_mcp_server.call_tool("get_alarm_history", {"asset_id": "BFP-101", "days": 90})
    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data["asset_id"] == "BFP-101"
    assert data["total_occurrences"] == 5
    assert len(data["top_recurring_alarms"]) >= 1
    assert data["top_recurring_alarms"][0]["alarm_code"] == "BFP-BEAR-TEMP-HI"


@pytest.mark.asyncio
async def test_get_correlations_execution(alarm_mcp_server):
    """Test discovering correlated equipment for CMP-201."""
    result = await alarm_mcp_server.call_tool("get_correlations", {"asset_ids": ["CMP-201"], "min_correlation": 0.8})
    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert "M-501" in data["correlated_asset_ids"]
    assert data["correlations"][0]["correlation_coefficient"] == 0.91


@pytest.mark.asyncio
async def test_analyze_alarm_hygiene_execution(alarm_mcp_server):
    """Test alarm hygiene flood and rationalization evaluation."""
    result = await alarm_mcp_server.call_tool(
        "analyze_alarm_hygiene",
        {"unit": "Unit 2", "threshold_count": 10, "rolling_window_minutes": 10},
    )
    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data["flood_events_count"] == 1
    assert len(data["rationalization_candidates"]) == 1
    assert data["rationalization_candidates"][0]["alarm_code"] == "CMP-DISCH-P-HI"


@pytest.mark.asyncio
async def test_search_knowledge_base_sop_execution(alarm_mcp_server):
    """Test RAG retrieval for compressor overpressure SOP."""
    result = await alarm_mcp_server.call_tool(
        "search_knowledge_base",
        {
            "query": "anti-surge valve positioner linkage troubleshooting",
            "doc_category": "sop",
            "asset_id": "CMP-201",
            "top_k": 2,
        },
    )
    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data["total_found"] >= 1
    first_citation = data["citations"][0]
    assert "SOP-CMP-201" in first_citation["filename"]
    assert "[Source:" in first_citation["citation"]
    assert first_citation["similarity_score"] > 0.0


@pytest.mark.asyncio
async def test_search_knowledge_base_escalation_policy(alarm_mcp_server):
    """Test RAG retrieval for P1 escalation SLA and notification policy."""
    result = await alarm_mcp_server.call_tool(
        "search_knowledge_base",
        {
            "query": "P1 incident escalation SLA tree 15 minutes",
            "doc_category": "escalation_policy",
            "top_k": 1,
        },
    )
    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data["total_found"] >= 1
    assert "ESCALATION" in data["citations"][0]["filename"]


@pytest.mark.asyncio
async def test_search_knowledge_base_database_mode():
    """Test RAG retrieval when database service returns active vector/keyword matches."""
    mock_chunk = ChunkRetrievalResult(
        chunk_id="chunk-db-1",
        document_id="doc-sop-201",
        filename="SOP-CMP-201-Discharge-Overpressure.md",
        file_type="md",
        chunk_index=2,
        content="## Step 2.1: Verify Anti-Surge Controller Status\nSwitch FIC-201 to MANUAL mode.",
        char_count=75,
        estimated_tokens=15,
        similarity_score=0.92,
    )
    mock_context = ChunkContextResponse(
        target_chunk=mock_chunk,
        preceding_chunks=[],
        following_chunks=[],
        expanded_content="## Step 2.1: Verify Anti-Surge Controller Status\nSwitch FIC-201 to MANUAL mode.\nRamp demand to 25%.",
        window_size=1,
    )

    doc_service = AsyncMock()
    doc_service.keyword_search.return_value = DocumentRetrievalResponse(
        query="anti-surge",
        results=[mock_chunk],
        total_results=1,
        search_type="keyword",
    )
    doc_service.get_chunk_context.return_value = mock_context

    client = BaseSimulatorClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    server = create_alarm_server(client=client, doc_service=doc_service)

    result = await server.call_tool(
        "search_knowledge_base",
        {
            "query": "anti-surge",
            "doc_category": "sop",
            "top_k": 1,
            "expand_context": True,
        },
    )
    assert not result.is_error
    data = json.loads(result.content[0].text)
    assert data["total_found"] == 1
    assert data["citations"][0]["chunk_id"] == "chunk-db-1"
    assert "Ramp demand to 25%" in data["citations"][0]["content"]
