"""Unit tests for BaseSimulatorClient: Auth, Retries, Per-request Policy, Trace Headers, and Exceptions."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from mcp_servers.client import BaseSimulatorClient
from mcp_servers.config import SimulatorConfig
from mcp_servers.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    SimulatorConnectionError,
    SimulatorServerError,
    ValidationError,
)


@pytest.fixture
def config():
    return SimulatorConfig(
        base_url="http://testserver",
        auth_token="secret-token-12345",
        timeout=5.0,
        max_retries=2,
        backoff_factor=0.1,
        client_id="unit-test-client",
        metadata_tag="test-tag",
    )


def test_config_token_masking(config):
    assert "secret-token-12345" not in repr(config)
    assert "secret-token-12345" not in str(config)
    assert "se***45" in repr(config)


@pytest.mark.asyncio
async def test_auth_and_trace_headers(config):
    captured_request = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with BaseSimulatorClient(config=config, transport=transport) as client:
        resp = await client.request("GET", "/test-endpoint", trace_id="custom-trace-001")
        assert resp.status_code == 200

    assert captured_request is not None
    assert captured_request.headers["Authorization"] == "Bearer secret-token-12345"
    assert captured_request.headers["trace_id"] == "custom-trace-001"
    assert captured_request.headers["trace-id"] == "custom-trace-001"
    assert captured_request.headers["x-client-id"] == "unit-test-client"
    assert captured_request.headers["x-metadata-tag"] == "test-tag"


@pytest.mark.asyncio
async def test_read_only_retry_on_503(config):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, json={"detail": "Service Unavailable"})
        return httpx.Response(200, json={"data": "recovered"})

    transport = httpx.MockTransport(handler)
    client = BaseSimulatorClient(config=config, transport=transport)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        resp = await client.request("GET", "/alarms")
        assert resp.status_code == 200
        assert resp.json()["data"] == "recovered"
        assert attempts == 3
        assert mock_sleep.call_count == 2

    await client.aclose()


@pytest.mark.asyncio
async def test_read_only_retry_on_read_timeout(config):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("Timeout reading response", request=request)
        return httpx.Response(200, json={"success": True})

    transport = httpx.MockTransport(handler)
    client = BaseSimulatorClient(config=config, transport=transport)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        resp = await client.request("POST", "/alarms/summary", json={})
        assert resp.status_code == 200
        assert attempts == 2

    await client.aclose()


@pytest.mark.asyncio
async def test_mutating_no_retry_on_read_timeout(config):
    """Amendment 1: Mutating requests (POST /tickets) MUST NOT retry on read timeouts."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("Timed out waiting for write to finish", request=request)

    transport = httpx.MockTransport(handler)
    client = BaseSimulatorClient(config=config, transport=transport)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(SimulatorConnectionError, match="Request timed out for POST /tickets"):
            await client.request("POST", "/tickets", json={"title": "Test Ticket"})
        assert attempts == 1
        assert mock_sleep.call_count == 0

    await client.aclose()


@pytest.mark.asyncio
async def test_mutating_no_retry_on_500(config):
    """Amendment 1: Mutating requests MUST NOT retry on 5xx."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, json={"detail": "Database error"})

    transport = httpx.MockTransport(handler)
    client = BaseSimulatorClient(config=config, transport=transport)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(SimulatorServerError, match="500"):
            await client.request("PATCH", "/tickets/INC-1001", json={"status": "closed"})
        assert attempts == 1
        assert mock_sleep.call_count == 0

    await client.aclose()


@pytest.mark.asyncio
async def test_mutating_retries_on_connect_error(config):
    """Amendment 1: Mutating requests CAN retry on connect errors (never reached server)."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("Failed to connect", request=request)
        return httpx.Response(201, json={"message": "Created", "ticket": {"ticket_id": "INC-1002"}})

    transport = httpx.MockTransport(handler)
    client = BaseSimulatorClient(config=config, transport=transport)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        resp = await client.request("POST", "/tickets", json={"title": "Test Ticket"})
        assert resp.status_code == 201
        assert attempts == 2
        assert mock_sleep.call_count == 1

    await client.aclose()


@pytest.mark.asyncio
async def test_exception_mappings(config):
    async def make_client(status: int, body: dict):
        transport = httpx.MockTransport(lambda req: httpx.Response(status, json=body))
        return BaseSimulatorClient(config=config, transport=transport)

    # 404 -> NotFoundError
    client = await make_client(404, {"detail": "Asset not found"})
    with pytest.raises(NotFoundError, match="Asset not found"):
        await client.request("GET", "/assets/missing/metadata")
    await client.aclose()

    # 400 -> ValidationError
    client = await make_client(400, {"detail": "Invalid query"})
    with pytest.raises(ValidationError, match="Invalid query"):
        await client.request("GET", "/assets/search")
    await client.aclose()

    # 401 -> AuthenticationError
    client = await make_client(401, {"detail": "Unauthorized"})
    with pytest.raises(AuthenticationError, match="Unauthorized"):
        await client.request("GET", "/assets/search")
    await client.aclose()

    # 409 -> ConflictError
    client = await make_client(409, {"detail": "Duplicate state"})
    with pytest.raises(ConflictError, match="Duplicate state"):
        await client.request("POST", "/test")
    await client.aclose()

    # 500 -> SimulatorServerError
    client = await make_client(500, {"detail": "Internal server crash"})
    with pytest.raises(SimulatorServerError, match="Internal server crash"):
        await client.request("GET", "/test")
    await client.aclose()
