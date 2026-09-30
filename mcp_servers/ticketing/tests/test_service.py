import json

import pytest

from ticketing_mcp.client import TicketingApiClient
from ticketing_mcp.errors import UnexpectedResponseError
from ticketing_mcp.models import TicketChanges, TicketDraft
from ticketing_mcp.service import TicketingService
from tests.conftest import FakeTicketingApi

DRAFT = TicketDraft(title="Compressor C-201 discharge overpressure", asset_id="CMP-201", severity="critical", priority="P1")


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


async def test_create_without_confirm_is_preview_only(service: TicketingService, fake_api: FakeTicketingApi) -> None:
    outcome = await service.create_ticket(DRAFT)

    assert outcome.committed is False
    assert outcome.pending_changes == DRAFT.model_dump()
    assert fake_api.requests == []


async def test_create_with_confirm_posts_the_draft(service: TicketingService, fake_api: FakeTicketingApi) -> None:
    outcome = await service.create_ticket(DRAFT, confirm=True)

    assert outcome.committed is True
    assert outcome.ticket is not None and outcome.ticket.ticket_id == "INC-2001"
    assert json.loads(fake_api.requests[0].content) == DRAFT.model_dump()


async def test_update_preview_reads_current_state_without_writing(
    service: TicketingService, fake_api: FakeTicketingApi
) -> None:
    outcome = await service.update_ticket("INC-1188", TicketChanges(status="resolved"))

    assert outcome.committed is False
    assert outcome.ticket is not None and outcome.ticket.status == "open"
    assert outcome.pending_changes == {"status": "resolved"}
    assert [r.method for r in fake_api.requests] == ["GET"]


async def test_update_with_confirm_sends_only_set_fields(service: TicketingService, fake_api: FakeTicketingApi) -> None:
    outcome = await service.update_ticket("INC-1188", TicketChanges(status="resolved"), confirm=True)

    assert outcome.committed is True
    assert json.loads(fake_api.requests[0].content) == {"status": "resolved"}


async def test_schema_drift_raises_unexpected_response(service: TicketingService, fake_api: FakeTicketingApi) -> None:
    fake_api.routes[("GET", "/tickets")] = {"items": []}

    with pytest.raises(UnexpectedResponseError):
        await service.find_tickets()
