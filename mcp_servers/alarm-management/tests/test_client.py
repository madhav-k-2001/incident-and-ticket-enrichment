import httpx
import pytest

from alarm_mcp.client import AlarmApiClient
from alarm_mcp.errors import AuthenticationError, NotFoundError, UnavailableError
from alarm_mcp.observability import bind_trace_id
from tests.conftest import FakeAlarmApi


async def test_sends_auth_client_and_trace_headers(api_client: AlarmApiClient, fake_api: FakeAlarmApi) -> None:
    bind_trace_id("trace-abc")

    await api_client.get_alarm("ALM-1")

    headers = fake_api.requests[0].headers
    assert headers["authorization"] == "Bearer secret-token"
    assert headers["trace_id"] == "trace-abc"
    assert headers["x-client-id"] == "alarm-mcp-server"


async def test_drops_unset_query_params(api_client: AlarmApiClient, fake_api: FakeAlarmApi) -> None:
    await api_client.list_alarms(asset_id="CMP-1", severity=None)

    assert dict(fake_api.requests[0].url.params) == {"asset_id": "CMP-1"}


async def test_encodes_ids_in_path(api_client: AlarmApiClient, fake_api: FakeAlarmApi) -> None:
    with pytest.raises(NotFoundError):
        await api_client.get_alarm("../tickets")

    assert fake_api.requests[0].url.raw_path == b"/alarms/..%2Ftickets"


async def test_retries_transient_failures_then_succeeds(api_client: AlarmApiClient, fake_api: FakeAlarmApi) -> None:
    responses = iter([httpx.Response(503), httpx.Response(502), httpx.Response(200, json={"ok": True})])
    fake_api.routes[("GET", "/alarms/ALM-1")] = lambda _: next(responses)

    assert await api_client.get_alarm("ALM-1") == {"ok": True}
    assert len(fake_api.requests) == 3


async def test_gives_up_after_max_retries(api_client: AlarmApiClient, fake_api: FakeAlarmApi) -> None:
    fake_api.fail("GET", "/alarms/ALM-1", 503)

    with pytest.raises(UnavailableError):
        await api_client.get_alarm("ALM-1")
    assert len(fake_api.requests) == 3  # 1 attempt + 2 retries


async def test_does_not_retry_client_errors(api_client: AlarmApiClient, fake_api: FakeAlarmApi) -> None:
    fake_api.fail("GET", "/alarms/ALM-1", 401)

    with pytest.raises(AuthenticationError) as exc_info:
        await api_client.get_alarm("ALM-1")
    assert "secret-token" not in str(exc_info.value)
    assert len(fake_api.requests) == 1


async def test_maps_timeouts_to_unavailable(api_client: AlarmApiClient, fake_api: FakeAlarmApi) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    fake_api.routes[("GET", "/alarms/ALM-1")] = timeout

    with pytest.raises(UnavailableError, match="timed out"):
        await api_client.get_alarm("ALM-1")
