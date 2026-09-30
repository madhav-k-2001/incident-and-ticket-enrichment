"""Typed contracts for tool inputs and outputs.

Tool inputs are strict (unknown fields rejected); source records ignore extra
fields so additive API changes don't break the server.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ticketing_mcp.observability import current_trace_id

Severity = Literal["critical", "high", "medium", "low"]
Priority = Literal["P1", "P2", "P3", "P4"]
Status = Literal["open", "in_progress", "resolved", "closed"]


# ---- Tool inputs ------------------------------------------------------------


class TicketDraft(BaseModel):
    """Fields for a new incident ticket."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=5, max_length=200, description="Short incident headline.")
    asset_id: str = Field(min_length=1, description="Affected asset, e.g. 'CMP-201'.")
    alarm_id: str | None = Field(default=None, description="Triggering alarm, e.g. 'ALM-7102'.")
    severity: Severity = "high"
    priority: Priority = "P2"
    symptom: str = Field(default="", description="Observed behaviour.")
    likely_cause: str = Field(default="", description="Suspected root cause.")
    recommended_action: str = Field(default="", description="Next steps for the assignee.")
    sop_reference: str = Field(default="", description="Applicable procedure, e.g. 'SOP-CMP-201 §2.3'.")
    assigned_to: str = Field(default="Instrumentation Team", description="Owning team.")
    assigned_user: str = Field(default="Unassigned", description="Owning individual.")
    similar_ticket_ref: str | None = Field(default=None, description="Related historical ticket id.")


class TicketChanges(BaseModel):
    """Partial update for an existing ticket. At least one field must be set."""

    model_config = ConfigDict(extra="forbid")

    status: Status | None = None
    priority: Priority | None = None
    resolution_notes: str | None = None
    assigned_to: str | None = None
    assigned_user: str | None = None
    work_notes: str | None = Field(default=None, description="Appended to the ticket audit trail.")

    @model_validator(mode="after")
    def _require_a_change(self) -> "TicketChanges":
        if not self.model_dump(exclude_none=True):
            raise ValueError("Provide at least one field to change.")
        return self


# ---- Source records ---------------------------------------------------------


class AuditEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    timestamp: str | None = None
    action: str | None = None
    note: str | None = None
    trace_id: str | None = None


class Ticket(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ticket_id: str
    asset_id: str
    alarm_id: str | None = None
    title: str
    status: str
    severity: str | None = None
    priority: str | None = None
    symptom: str | None = None
    root_cause: str | None = None
    likely_cause: str | None = None
    resolution_notes: str | None = None
    recommended_action: str | None = None
    sop_reference: str | None = None
    assigned_to: str | None = None
    assigned_user: str | None = None
    similar_ticket_ref: str | None = None
    created_at: str | None = None
    resolved_at: str | None = None
    audit_trail: list[AuditEntry] = Field(default_factory=list)
    relevance_score: int | None = Field(default=None, description="Only set by similarity search.")


# ---- Tool results -----------------------------------------------------------


class ToolResult(BaseModel):
    trace_id: str = Field(default_factory=current_trace_id, description="Correlates this result with server logs.")


class TicketList(ToolResult):
    tickets: list[Ticket]
    total: int = Field(description="Total matches before the limit was applied.")


class WriteOutcome(ToolResult):
    committed: bool = Field(description="False means preview only - nothing was written.")
    message: str
    ticket: Ticket | None = Field(default=None, description="Resulting ticket, or current state when previewing an update.")
    pending_changes: dict[str, Any] | None = Field(default=None, description="Payload awaiting confirmation.")
