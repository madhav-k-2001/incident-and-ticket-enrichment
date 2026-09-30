"""Shared Base HTTP Client for calling the Simulator API with retries, auth, and trace propagation."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, Optional, Set

import httpx

from mcp_servers.config import SimulatorConfig
from mcp_servers.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    SimulatorAPIError,
    SimulatorClientError,
    SimulatorConnectionError,
    SimulatorServerError,
    ValidationError,
)

logger = logging.getLogger("mcp_servers.client")

TRANSIENT_STATUS_CODES: Set[int] = {429, 502, 503, 504}


class BaseSimulatorClient:
    """
    Shared Base HTTP Client for all domain services.
    
    Features:
    - Bearer Auth with token redaction in logs/repr.
    - Distributed trace header propagation (trace_id, trace-id, x-client-id, x-metadata-tag).
    - Per-request retry policy:
        * GETs and read-only analytic POSTs retry on connect/timeout/429/502/503/504.
        * Mutating operations (POST /tickets, PATCH) retry ONLY on connection errors
          where the request never reached the server, NEVER on read timeouts or 5xx.
    - Exponential backoff on retries.
    - Typed domain exception translation.
    - Async context manager and aclose() lifecycle support.
    """

    def __init__(
        self,
        config: Optional[SimulatorConfig] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.config = config or SimulatorConfig()
        self._owns_http_client = http_client is None

        if http_client is not None:
            self._http_client = http_client
        else:
            self._http_client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=httpx.Timeout(self.config.timeout),
                transport=transport,
            )

    @property
    def http_client(self) -> httpx.AsyncClient:
        return self._http_client

    async def aclose(self) -> None:
        """Close the underlying HTTP client if owned."""
        if self._owns_http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def __aenter__(self) -> BaseSimulatorClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.aclose()

    def _is_read_only(self, method: str, path: str) -> bool:
        """
        Determine if request is read-only / safe for broad retry.
        GETs and analytical/recommendation POSTs are read-only.
        POST /tickets and PATCH requests are mutating.
        """
        m = method.upper()
        if m == "GET":
            return True
        if m in ("POST",) and (
            path.startswith("/alarms/")
            or path.startswith("/recommendations/")
            or path.startswith("/calculation-code/")
            or path.startswith("/analytics/")
        ):
            return True
        return False

    def _build_headers(
        self,
        trace_id: Optional[str] = None,
        metadata_tag: Optional[str] = None,
    ) -> Dict[str, str]:
        tid = trace_id or f"trace-{uuid.uuid4().hex[:8]}"
        headers = {
            "Accept": "application/json",
            "trace_id": tid,
            "trace-id": tid,
            "x-client-id": self.config.client_id,
            "x-metadata-tag": metadata_tag or self.config.metadata_tag,
        }
        if self.config.auth_token:
            headers["Authorization"] = f"Bearer {self.config.auth_token}"
        return headers

    def _mask_headers_for_logging(self, headers: Dict[str, str]) -> Dict[str, str]:
        """Return a copy of headers with secrets masked."""
        masked = dict(headers)
        if "Authorization" in masked:
            masked["Authorization"] = "Bearer [REDACTED]"
        return masked

    async def request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Any] = None,
        trace_id: Optional[str] = None,
        metadata_tag: Optional[str] = None,
    ) -> httpx.Response:
        """
        Execute an HTTP request with per-request retry policy and error mapping.
        """
        headers = self._build_headers(trace_id=trace_id, metadata_tag=metadata_tag)
        is_read_only = self._is_read_only(method, path)
        max_retries = self.config.max_retries
        backoff_factor = self.config.backoff_factor

        attempt = 0
        last_exception: Optional[Exception] = None

        while attempt <= max_retries:
            attempt += 1
            masked_headers = self._mask_headers_for_logging(headers)
            logger.debug(
                "HTTP request attempt %d/%d: %s %s params=%s headers=%s",
                attempt,
                max_retries + 1,
                method,
                path,
                params,
                masked_headers,
            )

            try:
                response = await self._http_client.request(
                    method=method,
                    url=path,
                    params=params,
                    json=json,
                    headers=headers,
                )

                # Check if response status is transient and eligible for retry
                if response.status_code in TRANSIENT_STATUS_CODES and is_read_only:
                    if attempt <= max_retries:
                        delay = backoff_factor * (2 ** (attempt - 1))
                        logger.warning(
                            "Transient status %d on %s %s. Retrying in %.2fs (attempt %d/%d)...",
                            response.status_code,
                            method,
                            path,
                            delay,
                            attempt,
                            max_retries,
                        )
                        await asyncio.sleep(delay)
                        continue

                # Raise or map response
                self._raise_for_status(response, method, path)
                return response

            except httpx.ConnectError as e:
                # Both read-only and mutating requests may retry if connection never reached server
                last_exception = e
                if attempt <= max_retries:
                    delay = backoff_factor * (2 ** (attempt - 1))
                    logger.warning(
                        "Connection error on %s %s. Retrying in %.2fs (attempt %d/%d): %s",
                        method,
                        path,
                        delay,
                        attempt,
                        max_retries,
                        e,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise SimulatorConnectionError(
                    f"Connection failed after {max_retries} retries: {e}"
                ) from e

            except httpx.TimeoutException as e:
                # Read timeouts are retried ONLY for read-only requests!
                last_exception = e
                if is_read_only and attempt <= max_retries:
                    delay = backoff_factor * (2 ** (attempt - 1))
                    logger.warning(
                        "Timeout on %s %s. Retrying in %.2fs (attempt %d/%d): %s",
                        method,
                        path,
                        delay,
                        attempt,
                        max_retries,
                        e,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise SimulatorConnectionError(
                    f"Request timed out for {method} {path}: {e}"
                ) from e

            except SimulatorAPIError:
                # Domain errors (4xx / 5xx) raised by _raise_for_status should bubble up
                raise

            except Exception as e:
                last_exception = e
                raise SimulatorClientError(
                    f"Unexpected client failure for {method} {path}: {e}"
                ) from e

        # If loop exhausted
        if last_exception:
            raise SimulatorConnectionError(
                f"Request exhausted all retries: {last_exception}"
            ) from last_exception
        raise SimulatorConnectionError(f"Request exhausted all retries for {method} {path}")

    def _raise_for_status(self, response: httpx.Response, method: str, path: str) -> None:
        """Map HTTP error status codes to typed domain exceptions."""
        if response.is_success:
            return

        status = response.status_code
        try:
            body = response.json()
            detail = body.get("detail", response.text)
        except Exception:
            body = response.text
            detail = response.text

        msg = f"{method} {path} failed: {detail}"

        if status == 404:
            raise NotFoundError(msg, status_code=status, response_body=body)
        elif status in (400, 422):
            raise ValidationError(msg, status_code=status, response_body=body)
        elif status in (401, 403):
            raise AuthenticationError(msg, status_code=status, response_body=body)
        elif status == 409:
            raise ConflictError(msg, status_code=status, response_body=body)
        elif status >= 500:
            raise SimulatorServerError(msg, status_code=status, response_body=body)
        else:
            raise SimulatorAPIError(msg, status_code=status, response_body=body)

    def __repr__(self) -> str:
        return f"BaseSimulatorClient(config={self.config!r})"
