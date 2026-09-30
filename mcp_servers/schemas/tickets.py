"""Pydantic schemas for Support and Incident Tickets."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from mcp_servers.schemas.common import Severity, TicketPriority, TicketStatus


class Ticket(BaseModel):
    """Ticket model matching mock_data.json."""

    model_config = ConfigDict(extra="allow")

    ticket_id: str
    asset_id: str
    alarm_id: Optional[str] = None
    title: str
    status: TicketStatus
    severity: Severity
    priority: TicketPriority
    symptom: Optional[str] = ""
    root_cause: Optional[str] = None
    likely_cause: Optional[str] = None
    resolution_notes: Optional[str] = None
    recommended_action: Optional[str] = None
    sop_reference: Optional[str] = None
    assigned_to: Optional[str] = None
    assigned_user: Optional[str] = None
    similar_ticket_ref: Optional[str] = None
    created_at: str
    resolved_at: Optional[str] = None
    audit_trail: Optional[List[Dict[str, Any]]] = None
    relevance_score: Optional[int] = None


class TicketCreateRequest(BaseModel):
    """Payload for creating a new incident ticket."""

    title: str
    asset_id: str
    alarm_id: Optional[str] = None
    severity: Optional[Severity] = "high"
    priority: Optional[TicketPriority] = "P2"
    symptom: Optional[str] = ""
    likely_cause: Optional[str] = ""
    recommended_action: Optional[str] = ""
    sop_reference: Optional[str] = ""
    assigned_to: Optional[str] = "Instrumentation Team"
    assigned_user: Optional[str] = "Unassigned"
    similar_ticket_ref: Optional[str] = None


class TicketUpdateRequest(BaseModel):
    """Payload for updating an existing ticket."""

    status: Optional[TicketStatus] = None
    priority: Optional[TicketPriority] = None
    resolution_notes: Optional[str] = None
    assigned_to: Optional[str] = None
    assigned_user: Optional[str] = None
    work_notes: Optional[str] = None


class TicketListResponse(BaseModel):
    """Raw response from GET /tickets."""

    model_config = ConfigDict(extra="ignore")

    tickets: List[Ticket] = Field(default_factory=list)
    total: int = 0


class TicketSearchResponse(BaseModel):
    """Raw response from GET /tickets/search."""

    model_config = ConfigDict(extra="ignore")

    query: str
    results: List[Ticket] = Field(default_factory=list)
    count: int = 0


class TicketSearchResult(BaseModel):
    """
    Composite result for TicketService.search_or_list_tickets.
    Includes cleaned keywords used, matched tickets after post-filtering, and total count.
    """

    model_config = ConfigDict(extra="ignore")

    query: Optional[str] = None
    cleaned_keywords: List[str] = Field(default_factory=list)
    matched_tickets: List[Ticket] = Field(default_factory=list)
    total_count: int = 0
    trace_id: Optional[str] = None


class TicketCreateResponse(BaseModel):
    """Raw response from POST /tickets."""

    model_config = ConfigDict(extra="ignore")

    message: str
    ticket: Ticket


class TicketCreateResult(BaseModel):
    """Composite result for TicketService.create_incident_ticket with idempotency tracking."""

    model_config = ConfigDict(extra="ignore")

    ticket: Ticket
    is_duplicate: bool = False
    message: str
    trace_id: Optional[str] = None


class TicketUpdateResponse(BaseModel):
    """Raw response from PATCH /tickets/{ticket_id}."""

    model_config = ConfigDict(extra="ignore")

    message: str
    ticket: Ticket


class TicketUpdateResult(BaseModel):
    """Composite result for TicketService.update_incident_ticket."""

    model_config = ConfigDict(extra="ignore")

    ticket: Ticket
    message: str
    trace_id: Optional[str] = None
