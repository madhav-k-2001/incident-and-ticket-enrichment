import httpx
import pytest

from ticketing_mcp.client import TicketingApiClient
from ticketing_mcp.errors import AuthenticationError, NotFoundError, UnavailableError
from ticketing_mcp.observability import bind_trace_id
from tests.conftest import FakeTicketingApi


async def test_sends_auth_client_and_trace_headers(api_client: TicketingApiClient, fake_api: FakeTicketingApi) -> None:
    bind_trace_id("trace-abc")

    await api_client.get_ticket("INC-1042")

    headers = fake_api.requests[0].headers
    assert headers["authorization"] == "Bearer secret-token"
    assert headers["trace-id"] == "trace-abc"
    assert headers["x-client-id"] == "ticketing-mcp"


async def test_drops_unset_query_params(api_client: TicketingApiClient, fake_api: FakeTicketingApi) -> None:
    await api_client.list_tickets(status=None, severity="high", asset_ids=["CMP-201", "M-501"], limit=5)

    assert dict(fake_api.requests[0].url.params) == {"severity": "high", "asset_ids": "CMP-201,M-501", "limit": "5"}


async def test_encodes_ids_in_path(api_client: TicketingApiClient, fake_api: FakeTicketingApi) -> None:
    with pytest.raises(NotFoundError):
        await api_client.get_ticket("../assets")

    assert fake_api.requests[0].url.raw_path == b"/tickets/..%2Fassets"


async def test_retries_reads_on_server_errors(api_client: TicketingApiClient, fake_api: FakeTicketingApi) -> None:
    responses = iter([httpx.Response(503), httpx.Response(502), httpx.Response(200, json={"ok": True})])
    fake_api.routes[("GET", "/tickets/INC-1042")] = lambda _: next(responses)

    assert await api_client.get_ticket("INC-1042") == {"ok": True}
    assert len(fake_api.requests) == 3


async def test_gives_up_after_max_retries(api_client: TicketingApiClient, fake_api: FakeTicketingApi) -> None:
    fake_api.fail("GET", "/tickets/INC-1042", 503)

    with pytest.raises(UnavailableError):
        await api_client.get_ticket("INC-1042")
    assert len(fake_api.requests) == 3  # 1 attempt + 2 retries


async def test_never_retries_writes(api_client: TicketingApiClient, fake_api: FakeTicketingApi) -> None:
    fake_api.fail("POST", "/tickets", 503)

    with pytest.raises(UnavailableError):
        await api_client.create_ticket({"title": "Some incident", "asset_id": "CMP-201"})
    assert len(fake_api.requests) == 1


async def test_maps_connection_failures_to_unavailable(
    api_client: TicketingApiClient, fake_api: FakeTicketingApi
) -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    fake_api.routes[("GET", "/tickets/INC-1042")] = refused

    with pytest.raises(UnavailableError, match="unreachable"):
        await api_client.get_ticket("INC-1042")


@pytest.mark.parametrize(
    ("status_code", "error"),
    [(404, NotFoundError), (401, AuthenticationError), (403, AuthenticationError)],
)
async def test_http_errors_map_to_domain_errors(
    api_client: TicketingApiClient, fake_api: FakeTicketingApi, status_code: int, error: type[Exception]
) -> None:
    fake_api.fail("GET", "/tickets/INC-1042", status_code, "nope")

    with pytest.raises(error):
        await api_client.get_ticket("INC-1042")
    assert len(fake_api.requests) == 1


async def test_auth_error_does_not_leak_token(api_client: TicketingApiClient, fake_api: FakeTicketingApi) -> None:
    fake_api.fail("GET", "/tickets/INC-1042", 401)

    with pytest.raises(AuthenticationError) as exc_info:
        await api_client.get_ticket("INC-1042")
    assert "secret-token" not in str(exc_info.value)
