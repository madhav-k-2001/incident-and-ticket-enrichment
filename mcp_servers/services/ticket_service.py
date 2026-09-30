"""TicketService for searching, listing, creating (idempotent), and updating tickets."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from mcp_servers.client import BaseSimulatorClient
from mcp_servers.schemas.common import Severity, TicketStatus
from mcp_servers.schemas.tickets import (
    Ticket,
    TicketCreateRequest,
    TicketCreateResponse,
    TicketCreateResult,
    TicketListResponse,
    TicketSearchResponse,
    TicketSearchResult,
    TicketUpdateRequest,
    TicketUpdateResponse,
    TicketUpdateResult,
)

STOPWORDS: Set[str] = {
    "the", "a", "an", "is", "for", "to", "on", "in", "with", "and", "or", "of",
    "at", "by", "from", "up", "about", "into", "over", "after", "related",
    "problem", "issue", "ticket", "tickets", "find", "search", "show", "get",
    "me", "prior", "similar", "please", "can", "you", "help", "view",
}


class TicketService:
    """Service for managing support and incident tickets with idempotency and clean search."""

    def __init__(
        self,
        client: BaseSimulatorClient,
        idempotency_ttl: float = 300.0,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.client = client
        self.idempotency_ttl = idempotency_ttl
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = asyncio.Lock()
        # Cache maps idempotency_key -> (Ticket, expiry_epoch_timestamp)
        self._idempotency_cache: Dict[str, Tuple[Ticket, float]] = {}

    async def search_or_list_tickets(
        self,
        query: Optional[str] = None,
        asset_ids: Optional[Union[List[str], str]] = None,
        status: Optional[TicketStatus] = None,
        severity: Optional[Severity] = None,
        limit: int = 20,
        trace_id: Optional[str] = None,
    ) -> TicketSearchResult:
        """
        Unified ticket discovery:
        - If query provided, cleans domain keywords (stripping stopwords).
        - If cleaning leaves no keywords, falls back to list mode (Amendment 5).
        - For multiple asset_ids with a query, searches without asset_id and post-filters.
        - Applies limit AFTER post-filtering.
        - If query omitted, queries list endpoint with asset_ids and status/severity.
        """
        tid = trace_id or f"trace-{uuid.uuid4().hex[:8]}"

        # Normalize asset_ids into a clean list
        asset_ids_list: List[str] = []
        if isinstance(asset_ids, str):
            asset_ids_list = [aid.strip() for aid in asset_ids.split(",") if aid.strip()]
        elif isinstance(asset_ids, list):
            asset_ids_list = [aid.strip() for aid in asset_ids if aid.strip()]

        asset_ids_upper = {aid.upper() for aid in asset_ids_list}

        # Clean keywords from query
        cleaned_tokens: List[str] = []
        if query:
            tokens = re.split(r"[^\w\-]+", query.lower())
            cleaned_tokens = [w for w in tokens if w and w not in STOPWORDS and len(w) > 1]

        # Case 1: Search mode with valid keywords
        if cleaned_tokens:
            cleaned_query = " ".join(cleaned_tokens)

            # Simulator search endpoint only supports a single asset_id or none.
            # If multiple asset_ids passed, search without asset_id and post-filter.
            target_asset_param: Optional[str] = None
            if len(asset_ids_list) == 1:
                target_asset_param = asset_ids_list[0]

            search_resp = await self._raw_search_tickets(
                query=cleaned_query,
                asset_id=target_asset_param,
                trace_id=tid,
            )
            raw_results = search_resp.results

            # Post-filter for multiple asset_ids if needed
            if len(asset_ids_list) > 1:
                raw_results = [t for t in raw_results if t.asset_id.upper() in asset_ids_upper]

            # Post-filter by status
            if status:
                raw_results = [t for t in raw_results if t.status.lower() == status.lower()]

            # Post-filter by severity
            if severity:
                raw_results = [t for t in raw_results if t.severity.lower() == severity.lower()]

            # Apply limit after post-filtering
            final_tickets = raw_results[:limit]

            return TicketSearchResult(
                query=query,
                cleaned_keywords=cleaned_tokens,
                matched_tickets=final_tickets,
                total_count=len(final_tickets),
                trace_id=tid,
            )

        # Case 2: List mode (query omitted or cleaned keywords were empty)
        list_params: Dict[str, Any] = {"limit": limit}
        if len(asset_ids_list) == 1:
            list_params["asset_id"] = asset_ids_list[0]
        elif len(asset_ids_list) > 1:
            list_params["asset_ids"] = ",".join(asset_ids_list)

        if status:
            list_params["status"] = status
        if severity:
            list_params["severity"] = severity

        list_resp = await self._raw_get_tickets(params=list_params, trace_id=tid)
        return TicketSearchResult(
            query=query,
            cleaned_keywords=[],
            matched_tickets=list_resp.tickets[:limit],
            total_count=len(list_resp.tickets[:limit]),
            trace_id=tid,
        )

    async def get_ticket(
        self,
        ticket_id: str,
        trace_id: Optional[str] = None,
    ) -> Ticket:
        """Retrieve single ticket by ID."""
        tid = trace_id or f"trace-{uuid.uuid4().hex[:8]}"
        response = await self.client.request(
            method="GET",
            path=f"/tickets/{ticket_id}",
            trace_id=tid,
        )
        return Ticket.model_validate(response.json())

    async def create_incident_ticket(
        self,
        request: TicketCreateRequest,
        idempotency_key: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> TicketCreateResult:
        """
        Create a new incident ticket with idempotency protection (Amendment 6).
        
        Guarded by an asyncio.Lock, respects TTL, caches only successful creations,
        uses idempotency_key with fingerprint as fallback.
        """
        tid = trace_id or f"trace-{uuid.uuid4().hex[:8]}"

        # Compute key: explicit key or fingerprint fallback
        effective_key = idempotency_key or (
            f"fp:{request.asset_id}:{request.alarm_id or ''}:{request.title.strip().lower()}"
        )

        now_monotonic = time.monotonic()

        # Check idempotency registry under lock
        async with self._lock:
            if effective_key in self._idempotency_cache:
                cached_ticket, expiry = self._idempotency_cache[effective_key]
                if now_monotonic < expiry:
                    return TicketCreateResult(
                        ticket=cached_ticket,
                        is_duplicate=True,
                        message=(
                            f"Duplicate ticket creation refused. Returning existing ticket {cached_ticket.ticket_id} "
                            f"(idempotency key: {effective_key!r})."
                        ),
                        trace_id=tid,
                    )
                else:
                    # Expired entry
                    del self._idempotency_cache[effective_key]

        # Execute creation call (only connection errors retry, read timeouts never retry)
        resp = await self._raw_post_ticket(payload=request, trace_id=tid)
        new_ticket = resp.ticket

        # Cache only successful creates under lock
        async with self._lock:
            self._idempotency_cache[effective_key] = (
                new_ticket,
                time.monotonic() + self.idempotency_ttl,
            )

        return TicketCreateResult(
            ticket=new_ticket,
            is_duplicate=False,
            message="Incident ticket created successfully",
            trace_id=tid,
        )

    async def update_incident_ticket(
        self,
        ticket_id: str,
        request: TicketUpdateRequest,
        trace_id: Optional[str] = None,
    ) -> TicketUpdateResult:
        """Update ticket status, priority, assignment, or add work notes."""
        tid = trace_id or f"trace-{uuid.uuid4().hex[:8]}"
        resp = await self._raw_patch_ticket(
            ticket_id=ticket_id,
            payload=request,
            trace_id=tid,
        )
        return TicketUpdateResult(
            ticket=resp.ticket,
            message=resp.message,
            trace_id=tid,
        )

    # --------------------------------------------------------------------------
    # Private Helper Methods
    # --------------------------------------------------------------------------

    async def _raw_get_tickets(
        self, params: Dict[str, Any], trace_id: Optional[str] = None
    ) -> TicketListResponse:
        response = await self.client.request(
            method="GET",
            path="/tickets",
            params=params,
            trace_id=trace_id,
        )
        return TicketListResponse.model_validate(response.json())

    async def _raw_search_tickets(
        self,
        query: str,
        asset_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> TicketSearchResponse:
        params: Dict[str, Any] = {"query": query}
        if asset_id:
            params["asset_id"] = asset_id

        response = await self.client.request(
            method="GET",
            path="/tickets/search",
            params=params,
            trace_id=trace_id,
        )
        return TicketSearchResponse.model_validate(response.json())

    async def _raw_post_ticket(
        self, payload: TicketCreateRequest, trace_id: Optional[str] = None
    ) -> TicketCreateResponse:
        response = await self.client.request(
            method="POST",
            path="/tickets",
            json=payload.model_dump(exclude_none=True),
            trace_id=trace_id,
        )
        return TicketCreateResponse.model_validate(response.json())

    async def _raw_patch_ticket(
        self,
        ticket_id: str,
        payload: TicketUpdateRequest,
        trace_id: Optional[str] = None,
    ) -> TicketUpdateResponse:
        response = await self.client.request(
            method="PATCH",
            path=f"/tickets/{ticket_id}",
            json=payload.model_dump(exclude_none=True),
            trace_id=trace_id,
        )
        return TicketUpdateResponse.model_validate(response.json())
