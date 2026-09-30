"""Unit tests for AlarmService: Lookup, Sorting, Time Filtering, Enrichment, and Fallback Detection."""

from __future__ import annotations

import httpx
import pytest

from mcp_servers.client import BaseSimulatorClient
from mcp_servers.config import SimulatorConfig
from mcp_servers.exceptions import NotFoundError
from mcp_servers.services.alarm_service import AlarmService


@pytest.fixture
def mock_alarm():
    return {
        "alarm_id": "ALM-9021",
        "asset_id": "CMP-201",
        "asset_name": "Wet Gas Compressor 201",
        "site": "EastRefinery",
        "unit": "Unit 2",
        "alarm_code": "CMP-DISCH-P-HI",
        "alarm_name": "Compressor Discharge Pressure High",
        "severity": "critical",
        "status": "active",
        "start_time": "2026-09-29T18:15:00Z",
        "end_time": None,
        "ack_time": None,
        "value": 48.2,
        "threshold": 42.0,
        "unit_of_measure": "bar",
        "priority_score": 94.5,
    }


@pytest.mark.asyncio
async def test_get_alarms_pagination_time_filter_and_sort(mock_alarm):
    """
    Amendment 3: Fetch all pages, apply client-side time-window filtering,
    sort by priority_score safely client-side, and return total_count and returned_count.
    """
    alarm1 = dict(mock_alarm, alarm_id="ALM-1", start_time="2026-09-20T10:00:00Z", priority_score=70.0)
    alarm2 = dict(mock_alarm, alarm_id="ALM-2", start_time="2026-09-25T10:00:00Z", priority_score=95.0)
    alarm3 = dict(mock_alarm, alarm_id="ALM-3", start_time="2026-09-28T10:00:00Z", priority_score=85.0)

    # Page 1 returns ALM-1 and ALM-2; Page 2 returns ALM-3
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        if page == 1:
            return httpx.Response(200, json={
                "data": [alarm1, alarm2],
                "pagination": {"page": 1, "page_size": 2, "total_count": 3, "total_pages": 2},
            })
        elif page == 2:
            return httpx.Response(200, json={
                "data": [alarm3],
                "pagination": {"page": 2, "page_size": 2, "total_count": 3, "total_pages": 2},
            })
        return httpx.Response(200, json={"data": [], "pagination": {"page": 3, "total_pages": 2}})

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    service = AlarmService(client)

    # Filter between 2026-09-22 and 2026-09-30 (should include ALM-2 and ALM-3, excluding ALM-1)
    # Sort by priority_score desc (ALM-2 [95.0] before ALM-3 [85.0]), limit=1
    result = await service.get_alarms(
        start_time="2026-09-22T00:00:00Z",
        end_time="2026-09-30T00:00:00Z",
        sort_by="priority_score",
        sort_order="desc",
        limit=1,
    )

    assert result.total_count == 2  # ALM-2 and ALM-3 matched time window
    assert result.returned_count == 1  # Limit=1
    assert len(result.alarms) == 1
    assert result.alarms[0].alarm_id == "ALM-2"
    assert result.alarms[0].priority_score == 95.0


@pytest.mark.asyncio
async def test_get_alarms_single_id(mock_alarm):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/alarms/ALM-9021":
            return httpx.Response(200, json=mock_alarm)
        return httpx.Response(404)

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    service = AlarmService(client)

    result = await service.get_alarms(alarm_id="ALM-9021")
    assert result.total_count == 1
    assert result.returned_count == 1
    assert result.alarms[0].alarm_id == "ALM-9021"


@pytest.mark.asyncio
async def test_enrich_alarm_context_success(mock_alarm):
    """Test full enrichment with parallel sub-calls and specific guidance."""
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/alarms/ALM-9021":
            return httpx.Response(200, json=mock_alarm)
        if p == "/assets/CMP-201/metadata":
            return httpx.Response(200, json={
                "asset_id": "CMP-201",
                "asset_name": "Wet Gas Compressor 201",
                "site": "EastRefinery",
                "unit": "Unit 2",
                "type": "compressor",
                "criticality": "critical",
            })
        if p == "/alarms/priority-score":
            return httpx.Response(200, json={
                "alarm_id": "ALM-9021",
                "priority_score": 94.5,
                "severity": "critical",
                "urgency": "immediate",
                "recommended_priority_level": "P1",
                "factors": {
                    "asset_criticality_weight": 1.5,
                    "safety_trip_proximity": "high",
                    "recurrence_penalty": 1.2,
                },
            })
        if p == "/recommendations/operator-actions":
            return httpx.Response(200, json={
                "alarm_id": "ALM-9021",
                "asset_id": "CMP-201",
                "likely_causes": ["Valve stem binding"],
                "immediate_actions": ["Command 25% open stroke"],
                "safety_precautions": ["Monitor acoustic sensors"],
                "related_assets": [],
                "historical_insights": "Past incident INC-1042 showed identical rise",
            })
        return httpx.Response(404)

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    service = AlarmService(client)

    context = await service.enrich_alarm_context("ALM-9021")
    assert context.alarm.alarm_id == "ALM-9021"
    assert context.asset is not None
    assert context.asset.asset_id == "CMP-201"
    assert context.priority_evaluation is not None
    assert context.priority_evaluation.priority_score == 94.5
    assert context.operator_guidance is not None
    assert context.operator_guidance_is_generic is False
    assert len(context.partial_errors) == 0


@pytest.mark.asyncio
async def test_enrich_alarm_context_detects_generic_guidance(mock_alarm):
    """Amendment 2: Generic fallback recommendation (asset_id UNKNOWN) is flagged."""
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/alarms/ALM-9021":
            return httpx.Response(200, json=mock_alarm)
        if p == "/assets/CMP-201/metadata":
            return httpx.Response(200, json={
                "asset_id": "CMP-201",
                "asset_name": "Compressor",
                "site": "East",
                "unit": "Unit 2",
                "type": "compressor",
                "criticality": "high",
            })
        if p == "/alarms/priority-score":
            return httpx.Response(200, json={
                "alarm_id": "ALM-9021",
                "priority_score": 75.0,
                "severity": "high",
                "urgency": "urgent",
                "recommended_priority_level": "P2",
                "factors": {"asset_criticality_weight": 1.2, "safety_trip_proximity": "normal", "recurrence_penalty": 1.2},
            })
        if p == "/recommendations/operator-actions":
            # Simulator fallback response with UNKNOWN asset_id
            return httpx.Response(200, json={
                "alarm_id": "ALM-9021",
                "asset_id": "UNKNOWN",
                "likely_causes": ["Sensor drift", "Operating setpoint exceeded"],
                "immediate_actions": ["Verify field gauge"],
                "safety_precautions": ["Follow standard plant safety PPE"],
                "related_assets": [],
                "historical_insights": "No specific prior correlation found.",
            })
        return httpx.Response(404)

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    service = AlarmService(client)

    context = await service.enrich_alarm_context("ALM-9021")
    assert context.operator_guidance is not None
    assert context.operator_guidance_is_generic is True


@pytest.mark.asyncio
async def test_enrich_alarm_context_invalid_alarm_raises_not_found():
    """Amendment 9: Validates alarm exists first, raising NotFoundError without calling sub-routes."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Alarm ALM-9999 not found"})

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    service = AlarmService(client)

    with pytest.raises(NotFoundError, match="Alarm ALM-9999 not found"):
        await service.enrich_alarm_context("ALM-9999")


@pytest.mark.asyncio
async def test_enrich_alarm_context_partial_failure_tolerance(mock_alarm):
    """Amendment 9: On sub-call failure, captures error in partial_errors and returns remaining sections."""
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/alarms/ALM-9021":
            return httpx.Response(200, json=mock_alarm)
        if p == "/assets/CMP-201/metadata":
            return httpx.Response(404, json={"detail": "Asset CMP-201 metadata not found"})
        if p == "/alarms/priority-score":
            return httpx.Response(200, json={
                "alarm_id": "ALM-9021",
                "priority_score": 80.0,
                "severity": "high",
                "urgency": "urgent",
                "recommended_priority_level": "P2",
                "factors": {"asset_criticality_weight": 1.2, "safety_trip_proximity": "normal", "recurrence_penalty": 1.2},
            })
        if p == "/recommendations/operator-actions":
            return httpx.Response(200, json={
                "alarm_id": "ALM-9021",
                "asset_id": "CMP-201",
                "likely_causes": [],
                "immediate_actions": [],
                "safety_precautions": [],
                "related_assets": [],
                "historical_insights": "",
            })
        return httpx.Response(404)

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    service = AlarmService(client)

    context = await service.enrich_alarm_context("ALM-9021")
    assert context.alarm.alarm_id == "ALM-9021"
    assert context.asset is None
    assert "asset" in context.partial_errors
    assert "404" in context.partial_errors["asset"]
    assert context.priority_evaluation is not None
    assert context.operator_guidance is not None
