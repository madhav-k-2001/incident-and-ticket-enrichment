import json

import pytest

from ticketing_mcp.client import TicketingApiClient
from ticketing_mcp.errors import UnexpectedResponseError
from ticketing_mcp.models import TicketChanges, TicketDraft
from ticketing_mcp.service import TicketingService
from tests.conftest import FakeTicketingApi

DRAFT = TicketDraft(
    title="Compressor C-201 discharge overpressure", asset_id="CMP-201", severity="critical", priority="P1"
)


@pytest.fixture
def service(api_client: TicketingApiClient) -> TicketingService:
    return TicketingService(api_client)


async def test_find_by_ticket_id_uses_exact_lookup(service: TicketingService, fake_api: FakeTicketingApi) -> None:
    result = await service.find_tickets(ticket_id="INC-1042", status="open")

    assert [t.ticket_id for t in result.tickets] == ["INC-1042"]
    assert result.total == 1
    assert fake_api.requests[0].url.path == "/tickets/INC-1042"


async def test_search_applies_limit_but_reports_total_matches(service: TicketingService) -> None:
    result = await service.search_similar_tickets("discharge pressure", limit=1)

    assert [t.ticket_id for t in result.tickets] == ["INC-1042"]
    assert result.total == 2


async def test_create_posts_the_draft(service: TicketingService, fake_api: FakeTicketingApi) -> None:
    outcome = await service.create_ticket(DRAFT)

    assert outcome.ticket.ticket_id == "INC-2001"
    assert json.loads(fake_api.requests[0].content) == DRAFT.model_dump()


async def test_update_sends_only_set_fields(service: TicketingService, fake_api: FakeTicketingApi) -> None:
    outcome = await service.update_ticket("INC-1188", TicketChanges(status="resolved"))

    assert outcome.ticket.status == "resolved"
    assert [r.method for r in fake_api.requests] == ["PATCH"]
    assert json.loads(fake_api.requests[0].content) == {"status": "resolved"}


async def test_schema_drift_raises_unexpected_response(service: TicketingService, fake_api: FakeTicketingApi) -> None:
    fake_api.routes[("GET", "/tickets")] = {"items": []}

    with pytest.raises(UnexpectedResponseError):
        await service.find_tickets()
