"""Common metadata, pagination, and Literal domain types."""

from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

# Literal types derived directly from mock_data.json
AlarmSortBy = Literal[
    "start_time",
    "priority_score",
    "severity",
    "alarm_name",
    "alarm_id",
    "value",
]

SortOrder = Literal["asc", "desc"]

AlarmStatus = Literal["active", "cleared"]

TicketStatus = Literal["open", "closed", "in_progress", "resolved"]

Severity = Literal["critical", "high", "medium", "low"]

TicketPriority = Literal["P1", "P2", "P3"]


class TraceMetadata(BaseModel):
    """Distributed tracing headers echoed by simulator."""

    model_config = ConfigDict(extra="ignore")

    trace_id: Optional[str] = None
    x_client_id: Optional[str] = None
    x_metadata_tag: Optional[str] = None


class TimeRange(BaseModel):
    """Time window filter schema."""

    start_time: Optional[str] = "2026-05-01T00:00:00Z"
    end_time: Optional[str] = "2026-10-01T00:00:00Z"


class PaginationMeta(BaseModel):
    """Pagination metadata from simulator responses."""

    model_config = ConfigDict(extra="ignore")

    page: int = 1
    page_size: int = 50
    total_count: int = 0
    total_pages: int = 1
