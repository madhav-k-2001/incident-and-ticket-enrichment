from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from alarm_mcp.client import AlarmApiClient
from alarm_mcp.config import Settings

ASSET = {
    "asset_id": "CMP-1",
    "asset_name": "Compressor 1",
    "site": "EastRefinery",
    "unit": "Unit 2",
    "type": "compressor",
    "criticality": "critical",
    "related_assets": ["M-1"],
}

ALARM = {
    "alarm_id": "ALM-1",
    "asset_id": "CMP-1",
    "asset_name": "Compressor 1",
    "site": "EastRefinery",
    "unit": "Unit 2",
    "alarm_code": "CMP-DISCH-P-HI",
    "alarm_name": "Discharge Pressure High",
    "severity": "critical",
    "status": "active",
    "start_time": "2026-09-29T18:15:00Z",
    "priority_score": 94.5,
}

Route = dict[str, Any] | Callable[[httpx.Request], httpx.Response]


class FakeAlarmApi:
    """In-memory stand-in for the Alarm Management API, used as an httpx transport."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], Route] = {
            ("GET", "/assets/search"): {"results": [ASSET], "total": 1},
            ("GET", "/assets/CMP-1/metadata"): ASSET,
            ("GET", "/alarms"): {
                "data": [ALARM],
                "pagination": {"page": 1, "page_size": 20, "total_count": 1, "total_pages": 1},
            },
            ("GET", "/alarms/ALM-1"): ALARM,
            ("POST", "/alarms/priority-score"): {
                "alarm_id": "ALM-1",
                "priority_score": 94.5,
                "urgency": "immediate",
                "recommended_priority_level": "P1",
                "factors": {},
            },
            ("POST", "/recommendations/operator-actions"): {
                "alarm_id": "ALM-1",
                "likely_causes": ["Anti-surge valve stuck"],
                "immediate_actions": ["Open recycle valve"],
            },
            ("POST", "/alarms/summary"): {
                "total_alarms": 1,
                "severity_breakdown": {"critical": 1, "high": 0, "medium": 0, "low": 0},
                "summary": [{"group_key": "Discharge Pressure High", "alarm_count": 1}],
                "kpis": {"alarm_count": 1, "recurring_rate": 0.0},
            },
            ("POST", "/alarms/trends"): {"data": [{"timestamp": "2026-09-29T00:00:00Z", "alarm_count": 1}]},
            ("POST", "/alarms/correlation"): {
                "correlations": [
                    {
                        "source_asset": "CMP-1",
                        "target_asset": "M-1",
                        "source_alarm_code": "CMP-DISCH-P-HI",
                        "target_alarm_code": "MTR-VIB-HI",
                        "correlation_coefficient": 0.91,
                    }
                ]
            },
        }

    def fail(self, method: str, path: str, status: int, detail: str = "failure") -> None:
        self.routes[(method, path)] = lambda _: httpx.Response(status, json={"detail": detail})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get((request.method, request.url.path))
        if route is None:
            return httpx.Response(404, json={"detail": f"{request.url.path} not found"})
        return route(request) if callable(route) else httpx.Response(200, json=route)


@pytest.fixture
def fake_api() -> FakeAlarmApi:
    return FakeAlarmApi()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        alarm_api_base_url="http://alarm-api.test",
        alarm_api_token="secret-token",
        alarm_api_max_retries=2,
    )


@pytest.fixture
async def api_client(settings: Settings, fake_api: FakeAlarmApi) -> AsyncIterator[AlarmApiClient]:
    async with AlarmApiClient(settings, transport=httpx.MockTransport(fake_api), backoff_seconds=0) as client:
        yield client
