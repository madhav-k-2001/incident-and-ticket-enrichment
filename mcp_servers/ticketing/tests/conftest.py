import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from ticketing_mcp.client import TicketingApiClient
from ticketing_mcp.config import Settings

TICKET = {
    "ticket_id": "INC-1042",
    "asset_id": "CMP-201",
    "alarm_id": "ALM-7102",
    "title": "Compressor discharge overpressure",
    "status": "resolved",
    "severity": "critical",
    "priority": "P1",
    "root_cause": "Anti-surge valve stuck",
    "resolution_notes": "Valve positioner recalibrated",
    "audit_trail": [],
}

OPEN_TICKET = {
    "ticket_id": "INC-1188",
    "asset_id": "M-501",
    "title": "Motor bearing vibration",
    "status": "open",
    "severity": "high",
    "priority": "P2",
    "audit_trail": [],
}

Route = dict[str, Any] | Callable[[httpx.Request], httpx.Response]


class FakeTicketingApi:
    """In-memory stand-in for the Ticketing API, used as an httpx transport."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], Route] = {
            ("GET", "/tickets"): {"tickets": [TICKET, OPEN_TICKET], "total": 2},
            ("GET", "/tickets/search"): {
                "query": "discharge pressure",
                "results": [{**TICKET, "relevance_score": 3}, {**OPEN_TICKET, "relevance_score": 1}],
                "count": 2,
            },
            ("GET", "/tickets/INC-1042"): TICKET,
            ("GET", "/tickets/INC-1188"): OPEN_TICKET,
            ("POST", "/tickets"): self._create,
            ("PATCH", "/tickets/INC-1188"): self._update,
        }

    def fail(self, method: str, path: str, status: int, detail: str = "failure") -> None:
        self.routes[(method, path)] = lambda _: httpx.Response(status, json={"detail": detail})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get((request.method, request.url.path))
        if route is None:
            return httpx.Response(404, json={"detail": f"{request.url.path} not found"})
        return route(request) if callable(route) else httpx.Response(200, json=route)

    @staticmethod
    def _create(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        ticket = {
            **body,
            "ticket_id": "INC-2001",
            "status": "open",
            "audit_trail": [{"action": "Ticket created", "trace_id": request.headers.get("trace-id")}],
        }
        return httpx.Response(201, json={"message": "created", "ticket": ticket})

    @staticmethod
    def _update(request: httpx.Request) -> httpx.Response:
        changes = json.loads(request.content)
        ticket = {**OPEN_TICKET, **changes}
        if changes.get("status") in ("resolved", "closed"):
            ticket["resolved_at"] = "2026-09-30T00:00:00Z"
        return httpx.Response(200, json={"message": "updated", "ticket": ticket})


@pytest.fixture
def fake_api() -> FakeTicketingApi:
    return FakeTicketingApi()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        ticketing_api_base_url="http://ticketing-api.test",
        ticketing_api_token="secret-token",
        ticketing_max_retries=2,
        ticketing_retry_backoff_seconds=0,
    )


@pytest.fixture
async def api_client(settings: Settings, fake_api: FakeTicketingApi) -> AsyncIterator[TicketingApiClient]:
    async with TicketingApiClient(settings, transport=httpx.MockTransport(fake_api)) as client:
        yield client
