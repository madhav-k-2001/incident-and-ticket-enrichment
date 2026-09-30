"""Async HTTP client for the Alarm Management API.

Owns transport concerns only: auth, timeouts, retries, trace headers and error
mapping. One method per API endpoint; responses are returned as raw JSON.
"""

import asyncio
import logging
import time
from types import TracebackType
from typing import Any
from urllib.parse import quote

import httpx

from alarm_mcp.config import Settings
from alarm_mcp.errors import AlarmApiError, UnavailableError, error_for_status
from alarm_mcp.observability import current_trace_id

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 502, 503, 504})


class AlarmApiClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        backoff_seconds: float = 0.5,
    ) -> None:
        headers = {"x-client-id": settings.alarm_api_client_id}
        if token := settings.alarm_api_token.get_secret_value():
            headers["Authorization"] = f"Bearer {token}"

        self._http = httpx.AsyncClient(
            base_url=str(settings.alarm_api_base_url),
            headers=headers,
            timeout=settings.alarm_api_timeout_seconds,
            transport=transport,
        )
        self._max_retries = settings.alarm_api_max_retries
        self._backoff_seconds = backoff_seconds

    async def __aenter__(self) -> "AlarmApiClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._http.aclose()

    # ---- Assets -------------------------------------------------------------

    async def search_assets(
        self, *, query: str | None, site: str | None, unit: str | None, limit: int
    ) -> dict[str, Any]:
        return await self._get("/assets/search", query=query, site=site, unit=unit, limit=limit)

    async def get_asset(self, asset_id: str) -> dict[str, Any]:
        return await self._get(f"/assets/{quote(asset_id, safe='')}/metadata")

    # ---- Alarms -------------------------------------------------------------

    async def list_alarms(self, **filters: Any) -> dict[str, Any]:
        return await self._get("/alarms", **filters)

    async def get_alarm(self, alarm_id: str) -> dict[str, Any]:
        return await self._get(f"/alarms/{quote(alarm_id, safe='')}")

    async def alarm_summary(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/alarms/summary", body)

    async def alarm_trends(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/alarms/trends", body)

    async def alarm_correlation(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/alarms/correlation", body)

    async def priority_score(self, alarm_id: str) -> dict[str, Any]:
        return await self._post("/alarms/priority-score", {"alarm_id": alarm_id})

    async def operator_recommendations(self, alarm_id: str) -> dict[str, Any]:
        return await self._post("/recommendations/operator-actions", {"alarm_id": alarm_id})

    # ---- Transport ----------------------------------------------------------

    async def _get(self, path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        return await self._request("GET", path, params=clean)

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        clean = {k: v for k, v in body.items() if v is not None}
        return await self._request("POST", path, json=clean)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"trace_id": current_trace_id(), "x-metadata-tag": "mcp"}
        error: AlarmApiError = UnavailableError("Alarm API request was not attempted.")

        for attempt in range(1, self._max_retries + 2):
            started = time.perf_counter()
            try:
                response = await self._http.request(method, path, headers=headers, **kwargs)
            except httpx.TimeoutException:
                error = UnavailableError("Alarm API timed out. Try again later.")
            except httpx.TransportError:
                error = UnavailableError("Alarm API is unreachable. Try again later.")
            else:
                self._log(method, path, attempt, started, response.status_code)
                if response.is_success:
                    return response.json()
                error = error_for_status(response.status_code, _detail(response))
                if response.status_code not in RETRYABLE_STATUS:
                    raise error

            if attempt <= self._max_retries:
                fields = {"path": path, "attempt": attempt, "reason": str(error)}
                logger.warning("alarm_api_retry", extra={"fields": fields})
                await asyncio.sleep(self._backoff_seconds * 2 ** (attempt - 1))

        raise error

    @staticmethod
    def _log(method: str, path: str, attempt: int, started: float, status: int) -> None:
        fields = {
            "method": method,
            "path": path,
            "attempt": attempt,
            "status": status,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        logger.info("alarm_api_call", extra={"fields": fields})


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail", response.reason_phrase)
    except (ValueError, AttributeError):
        detail = response.reason_phrase
    return str(detail)[:200]
