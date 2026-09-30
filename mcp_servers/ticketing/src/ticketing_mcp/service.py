"""Use cases exposed as MCP tools, including the write-confirmation policy.

Composes raw API calls into task-oriented results and validates every payload
against the typed contracts. Knows nothing about MCP or HTTP.
"""

import logging
from typing import Any

from pydantic import BaseModel, ValidationError

from ticketing_mcp.client import TicketingApiClient
from ticketing_mcp.errors import UnexpectedResponseError
from ticketing_mcp.models import Severity, Status, Ticket, TicketChanges, TicketDraft, TicketList, WriteOutcome

logger = logging.getLogger(__name__)

PREVIEW_MESSAGE = "Preview only - nothing was written. Ask the operator to approve, then call again with confirm=true."


class TicketingService:
    def __init__(self, api: TicketingApiClient) -> None:
        self._api = api

    async def find_tickets(
        self,
        *,
        ticket_id: str | None = None,
        asset_ids: list[str] | None = None,
        status: Status | None = None,
        severity: Severity | None = None,
        limit: int = 20,
    ) -> TicketList:
        if ticket_id:
            ticket = await self._api.get_ticket(ticket_id)
            return _parse(TicketList, {"tickets": [ticket], "total": 1})

        data = await self._api.list_tickets(status=status, severity=severity, asset_ids=asset_ids, limit=limit)
        return _parse(TicketList, {"tickets": data.get("tickets"), "total": data.get("total")})

    async def search_similar_tickets(self, query: str, *, asset_id: str | None = None, limit: int = 5) -> TicketList:
        data = await self._api.search_tickets(query=query, asset_id=asset_id)
        return _parse(TicketList, {"tickets": (data.get("results") or [])[:limit], "total": data.get("count")})

    async def create_ticket(self, draft: TicketDraft, *, confirm: bool = False) -> WriteOutcome:
        if not confirm:
            return WriteOutcome(committed=False, message=PREVIEW_MESSAGE, pending_changes=draft.model_dump())

        data = await self._api.create_ticket(draft.model_dump())
        ticket = _parse(Ticket, data.get("ticket"))
        logger.info("ticket_created", extra={"fields": {"ticket_id": ticket.ticket_id}})
        return WriteOutcome(committed=True, message=f"Ticket {ticket.ticket_id} created.", ticket=ticket)

    async def update_ticket(self, ticket_id: str, changes: TicketChanges, *, confirm: bool = False) -> WriteOutcome:
        pending = changes.model_dump(exclude_none=True)
        if not confirm:
            current = _parse(Ticket, await self._api.get_ticket(ticket_id))
            return WriteOutcome(committed=False, message=PREVIEW_MESSAGE, ticket=current, pending_changes=pending)

        data = await self._api.update_ticket(ticket_id, pending)
        ticket = _parse(Ticket, data.get("ticket"))
        logger.info("ticket_updated", extra={"fields": {"ticket_id": ticket_id, "changed": sorted(pending)}})
        return WriteOutcome(committed=True, message=f"Ticket {ticket_id} updated.", ticket=ticket)


# ---- Helpers ----------------------------------------------------------------


def _parse[M: BaseModel](model: type[M], data: Any) -> M:
    """Validate an API-derived payload; schema drift surfaces as a clear domain error."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        fields = {"model": model.__name__, "errors": exc.errors(include_input=False)}
        logger.error("unexpected_api_response", extra={"fields": fields})
        raise UnexpectedResponseError(
            f"Ticketing API returned data that does not match the expected {model.__name__} shape."
        ) from exc
