"""Async HTTP client for the Ticketing API.

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

from ticketing_mcp.config import Settings
from ticketing_mcp.errors import TicketingApiError, UnavailableError, error_for_status
from ticketing_mcp.observability import current_trace_id

logger = logging.getLogger(__name__)

# Only safe-to-repeat methods are retried; retrying a POST could create duplicate tickets.
RETRYABLE_METHODS = frozenset({"GET"})


class TicketingApiClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        headers = {"Accept": "application/json", "x-client-id": settings.ticketing_client_id}
        if token := settings.ticketing_api_token.get_secret_value():
            headers["Authorization"] = f"Bearer {token}"

        self._http = httpx.AsyncClient(
            base_url=str(settings.ticketing_api_base_url),
            headers=headers,
            timeout=settings.ticketing_timeout_seconds,
            transport=transport,
        )
        self._max_retries = settings.ticketing_max_retries
        self._backoff_seconds = settings.ticketing_retry_backoff_seconds

    async def __aenter__(self) -> "TicketingApiClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._http.aclose()

    # ---- Tickets ------------------------------------------------------------

    async def list_tickets(
        self, *, status: str | None, severity: str | None, asset_ids: list[str] | None, limit: int
    ) -> dict[str, Any]:
        joined = ",".join(asset_ids) if asset_ids else None
        return await self._get("/tickets", status=status, severity=severity, asset_ids=joined, limit=limit)

    async def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        return await self._get(f"/tickets/{quote(ticket_id, safe='')}")

    async def search_tickets(self, *, query: str, asset_id: str | None) -> dict[str, Any]:
        return await self._get("/tickets/search", query=query, asset_id=asset_id)

    async def create_ticket(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/tickets", json=body)

    async def update_ticket(self, ticket_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PATCH", f"/tickets/{quote(ticket_id, safe='')}", json=body)

    # ---- Transport ----------------------------------------------------------

    async def _get(self, path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        return await self._request("GET", path, params=clean or None)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"trace-id": current_trace_id()}
        attempts = 1 + (self._max_retries if method in RETRYABLE_METHODS else 0)
        error: TicketingApiError = UnavailableError("Ticketing API request was not attempted.")

        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                response = await self._http.request(method, path, headers=headers, **kwargs)
            except httpx.TransportError:  # includes timeouts and connection errors
                error = UnavailableError("Ticketing service is unreachable or timed out.")
            else:
                self._log(method, path, attempt, started, response.status_code)
                if response.status_code < 400:
                    return response.json()
                error = error_for_status(response.status_code, _detail(response))
                if response.status_code < 500:
                    raise error

            if attempt < attempts:
                fields = {"method": method, "path": path, "attempt": attempt, "reason": str(error)}
                logger.warning("ticketing_api_retry", extra={"fields": fields})
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
        logger.info("ticketing_api_call", extra={"fields": fields})


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    return str(body.get("detail", "")) if isinstance(body, dict) else ""
