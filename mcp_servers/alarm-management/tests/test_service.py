import json
from datetime import datetime

import pytest

from alarm_mcp.client import AlarmApiClient
from alarm_mcp.errors import InvalidRequestError, NotFoundError, UnexpectedResponseError
from alarm_mcp.service import AlarmService
from tests.conftest import FakeAlarmApi


@pytest.fixture
def service(api_client: AlarmApiClient) -> AlarmService:
    return AlarmService(api_client)


async def test_search_by_asset_id_uses_exact_lookup(service: AlarmService, fake_api: FakeAlarmApi) -> None:
    result = await service.search_assets(asset_id="CMP-1")

    assert [a.asset_id for a in result.assets] == ["CMP-1"]
    assert fake_api.requests[0].url.path == "/assets/CMP-1/metadata"


async def test_alarm_context_combines_all_sources(service: AlarmService) -> None:
    context = await service.get_alarm_context("ALM-1")

    assert context.alarm.alarm_id == "ALM-1"
    assert context.asset is not None and context.asset.asset_id == "CMP-1"
    assert context.priority is not None and context.priority.recommended_priority_level == "P1"
    assert context.recommendations is not None and context.recommendations.likely_causes
    assert context.warnings == []


async def test_alarm_context_returns_partial_result_when_enrichment_fails(
    service: AlarmService, fake_api: FakeAlarmApi
) -> None:
    fake_api.fail("POST", "/recommendations/operator-actions", 500)

    context = await service.get_alarm_context("ALM-1")

    assert context.recommendations is None
    assert context.priority is not None
    assert len(context.warnings) == 1 and "operator recommendations" in context.warnings[0]


async def test_alarm_context_fails_when_alarm_missing(service: AlarmService) -> None:
    with pytest.raises(NotFoundError):
        await service.get_alarm_context("ALM-404")


async def test_analytics_tolerates_missing_trend(service: AlarmService, fake_api: FakeAlarmApi) -> None:
    fake_api.fail("POST", "/alarms/trends", 500)

    analytics = await service.analyze_alarms(asset_ids=["CMP-1"])

    assert analytics.total_alarms == 1
    assert analytics.trend == []
    assert analytics.warnings


async def test_time_window_is_normalised_to_utc(service: AlarmService, fake_api: FakeAlarmApi) -> None:
    await service.find_correlated_alarms(
        start_time=datetime.fromisoformat("2026-09-01T02:00:00+02:00"),
        end_time=datetime.fromisoformat("2026-09-02T00:00:00"),
    )

    body = json.loads(fake_api.requests[0].content)
    assert body["time_range"] == {"start_time": "2026-09-01T00:00:00Z", "end_time": "2026-09-02T00:00:00Z"}


async def test_rejects_inverted_time_window(service: AlarmService) -> None:
    with pytest.raises(InvalidRequestError):
        await service.analyze_alarms(start_time=datetime(2026, 9, 2), end_time=datetime(2026, 9, 1))


async def test_schema_drift_raises_clear_error(service: AlarmService, fake_api: FakeAlarmApi) -> None:
    fake_api.routes[("GET", "/alarms/ALM-1")] = {"alarm_id": "ALM-1"}

    with pytest.raises(UnexpectedResponseError):
        await service.get_alarm_context("ALM-1")
