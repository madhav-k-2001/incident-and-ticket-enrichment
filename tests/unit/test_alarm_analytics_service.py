"""Unit tests for AlarmAnalyticsService: Correlation, Recurrence History, and Hygiene."""

from __future__ import annotations

from datetime import datetime, timezone
import httpx
import pytest

from mcp_servers.client import BaseSimulatorClient
from mcp_servers.config import SimulatorConfig
from mcp_servers.services.alarm_analytics_service import AlarmAnalyticsService


@pytest.fixture
def mock_correlations():
    return [
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
        },
        {
            "source_asset": "BFP-101",
            "target_asset": "BFP-101",
            "source_alarm_code": "BFP-LUBE-OIL-P-LO",
            "target_alarm_code": "BFP-BEAR-TEMP-HI",
            "correlation_coefficient": 0.84,
            "avg_lag_minutes": 4.0,
            "cooccurrence_count": 3,
            "significance": "high",
            "description": "Lube oil pressure dip triggers rise in thrust bearing temp",
        },
        {
            "source_asset": "TK-401",
            "target_asset": "TK-402",
            "source_alarm_code": "TK-LVL-LO",
            "target_alarm_code": "TK-LVL-LO",
            "correlation_coefficient": 0.45,  # Low correlation
            "avg_lag_minutes": 10.0,
            "cooccurrence_count": 1,
            "significance": "low",
            "description": "Low correlation",
        },
    ]


@pytest.mark.asyncio
async def test_get_correlations_filters_and_excludes_queried(mock_correlations):
    """
    Amendment 7:
    - min_correlation=0.8 keeps 0.91 and 0.84, filters out 0.45.
    - filters to queried assets (CMP-201).
    - excludes queried asset CMP-201 from correlated_asset_ids (returns only M-501).
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "correlation_method": "cooccurrence",
            "lag_window_minutes": 15,
            "correlations": mock_correlations,
        })

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    service = AlarmAnalyticsService(client)

    result = await service.get_correlations(asset_ids=["CMP-201"], min_correlation=0.8)

    assert len(result.correlations) == 1
    assert result.correlations[0].source_asset == "CMP-201"
    assert result.correlations[0].target_asset == "M-501"
    assert result.queried_asset_ids == ["CMP-201"]
    assert result.correlated_asset_ids == ["M-501"]  # CMP-201 excluded!


@pytest.mark.asyncio
async def test_get_alarm_history_dynamic_recurrence():
    """
    Amendment 8:
    - Uses injectable clock.
    - Dynamically computes recurrence rate, severity breakdown, and daily buckets from real alarms.
    """
    mock_alarms = [
        {"alarm_code": "BFP-BEAR-TEMP-HI", "alarm_name": "Bearing Temp High", "severity": "high", "start_time": "2026-09-18T08:12:00Z"},
        {"alarm_code": "BFP-BEAR-TEMP-HI", "alarm_name": "Bearing Temp High", "severity": "high", "start_time": "2026-09-02T14:30:00Z"},
        {"alarm_code": "BFP-BEAR-TEMP-HI", "alarm_name": "Bearing Temp High", "severity": "critical", "start_time": "2026-08-14T03:20:00Z"},
        {"alarm_code": "BFP-LUBE-OIL-P-LO", "alarm_name": "Lube Oil Low", "severity": "high", "start_time": "2026-08-14T03:18:00Z"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "data": mock_alarms,
            "pagination": {"page": 1, "page_size": 50, "total_count": 4, "total_pages": 1},
        })

    # Fixed clock at 2026-09-30T00:00:00Z
    fixed_clock = lambda: datetime(2026, 9, 30, 0, 0, 0, tzinfo=timezone.utc)

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    service = AlarmAnalyticsService(client, clock=fixed_clock)

    history = await service.get_alarm_history(asset_id="BFP-101", days=90)

    assert history.asset_id == "BFP-101"
    assert history.evaluation_period_days == 90
    assert history.total_occurrences == 4
    # 3 out of 4 are recurring (BFP-BEAR-TEMP-HI occurs 3 times) => 3/4 = 0.75
    assert history.recurring_rate == 0.75
    assert history.severity_breakdown["high"] == 3
    assert history.severity_breakdown["critical"] == 1
    assert len(history.top_recurring_alarms) == 2
    assert history.top_recurring_alarms[0].alarm_code == "BFP-BEAR-TEMP-HI"
    assert history.top_recurring_alarms[0].count == 3
    # Daily trend counts
    assert len(history.daily_trend) == 3


@pytest.mark.asyncio
async def test_analyze_alarm_hygiene_parallel_and_filtering():
    """Test parallel execution of flood analysis and rationalization candidates with asset filtering."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/alarms/flood-analysis":
            return httpx.Response(200, json={
                "unit": "Unit 2",
                "threshold_count": 10,
                "rolling_window_minutes": 10,
                "flood_events_count": 1,
                "flood_windows": [{
                    "start": "2026-06-15T14:00:00Z",
                    "end": "2026-06-15T14:10:00Z",
                    "alarm_count": 14,
                    "peak_rate_per_min": 3.8,
                    "primary_contributing_assets": ["CMP-201", "M-501"],
                }],
            })
        if request.url.path == "/alarms/rationalization-candidates":
            return httpx.Response(200, json={
                "candidates": [
                    {
                        "alarm_id": "ALM-6001",
                        "asset_id": "CMP-201",
                        "alarm_code": "CMP-SEAL-DP-LOW",
                        "alarm_name": "Dry Gas Seal Differential Pressure Low",
                        "classification": "nuisance",
                        "recurrence_count": 18,
                        "stale_duration_minutes": 0,
                        "justification": "Self-clears within 30s",
                        "recommended_action": "Apply 45s on-delay",
                    },
                    {
                        "alarm_id": "ALM-6004",
                        "asset_id": "TK-401",
                        "alarm_code": "TK-LVL-LO-WARN",
                        "alarm_name": "Storage Tank Level Low Advisory",
                        "classification": "stale",
                        "recurrence_count": 1,
                        "stale_duration_minutes": 4320,
                        "justification": "Decommissioned",
                        "recommended_action": "Shelf alarm",
                    },
                ]
            })
        return httpx.Response(404)

    client = BaseSimulatorClient(config=SimulatorConfig(), transport=httpx.MockTransport(handler))
    service = AlarmAnalyticsService(client)

    # Filter to CMP-201 only
    report = await service.analyze_alarm_hygiene(unit="Unit 2", asset_ids=["CMP-201"])
    assert report.flood_events_count == 1
    assert len(report.rationalization_candidates) == 1
    assert report.rationalization_candidates[0].asset_id == "CMP-201"
    assert len(report.partial_errors) == 0
