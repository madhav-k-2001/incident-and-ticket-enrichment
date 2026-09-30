"""Pydantic schemas for Alarms, Enrichment, and Context."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from mcp_servers.schemas.assets import AssetMetadata
from mcp_servers.schemas.common import (
    AlarmStatus,
    PaginationMeta,
    Severity,
    TimeRange,
    TraceMetadata,
)


class Alarm(BaseModel):
    """Alarm representation matching mock_data.json."""

    model_config = ConfigDict(extra="allow")

    alarm_id: str
    asset_id: str
    asset_name: str
    site: str
    unit: str
    alarm_code: str
    alarm_name: str
    severity: Severity
    status: AlarmStatus
    start_time: str
    end_time: Optional[str] = None
    ack_time: Optional[str] = None
    value: float
    threshold: float
    unit_of_measure: str
    priority_score: Optional[float] = None


class AlarmListResponse(BaseModel):
    """Raw simulator response for GET /alarms."""

    model_config = ConfigDict(extra="ignore")

    data: List[Alarm] = Field(default_factory=list)
    pagination: PaginationMeta = Field(default_factory=PaginationMeta)


class AlarmLookupResult(BaseModel):
    """
    Composite result for AlarmService.get_alarms.
    Contains filtered, sorted alarms, total count before limit, and returned count.
    """

    model_config = ConfigDict(extra="ignore")

    alarms: List[Alarm] = Field(default_factory=list)
    total_count: int
    returned_count: int
    applied_filters: Dict[str, Any] = Field(default_factory=dict)
    trace_id: Optional[str] = None


class AlarmSeverityBreakdown(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0


class AlarmGroupSummary(BaseModel):
    group_key: str
    alarm_count: int
    recurring_rate: float
    avg_ack_delay_seconds: float


class AlarmKPIs(BaseModel):
    alarm_count: int
    recurring_rate: float
    avg_ack_delay_seconds: float


class AlarmSummaryRequest(BaseModel):
    asset_ids: Optional[List[str]] = Field(default_factory=list)
    time_range: Optional[TimeRange] = None
    severity: Optional[List[str]] = None
    group_by: Optional[List[str]] = Field(default_factory=lambda: ["alarm_name"])
    kpis: Optional[List[str]] = Field(
        default_factory=lambda: ["alarm_count", "recurring_rate", "avg_ack_delay"]
    )


class AlarmSummaryResponse(BaseModel):
    total_alarms: int
    severity_breakdown: AlarmSeverityBreakdown
    summary: List[AlarmGroupSummary]
    kpis: AlarmKPIs
    trace_metadata: Optional[TraceMetadata] = None


class PriorityScoreFactors(BaseModel):
    model_config = ConfigDict(extra="allow")

    asset_criticality_weight: float
    safety_trip_proximity: str
    recurrence_penalty: float


class PriorityScoreResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    alarm_id: str
    priority_score: float
    severity: str
    urgency: str
    recommended_priority_level: str
    factors: PriorityScoreFactors


class RelatedAssetInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    asset_id: str
    asset_name: str
    role: str
    status: str


class OperatorRecommendationResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    alarm_id: str
    asset_id: str
    likely_causes: List[str] = Field(default_factory=list)
    immediate_actions: List[str] = Field(default_factory=list)
    safety_precautions: List[str] = Field(default_factory=list)
    related_assets: List[RelatedAssetInfo] = Field(default_factory=list)
    historical_insights: str = ""
    trace_metadata: Optional[TraceMetadata] = None


class EnrichedAlarmContext(BaseModel):
    """
    Composite enriched alarm context:
    Combines alarm details, asset specifications, priority score, and recommendations.
    Tolerates partial failures by reporting errors in partial_errors.
    Flags generic guidance when recommendations return fallback data for unknown entries.
    """

    model_config = ConfigDict(extra="ignore")

    alarm: Alarm
    asset: Optional[AssetMetadata] = None
    priority_evaluation: Optional[PriorityScoreResponse] = None
    operator_guidance: Optional[OperatorRecommendationResponse] = None
    operator_guidance_is_generic: bool = False
    partial_errors: Dict[str, str] = Field(default_factory=dict)
    trace_id: Optional[str] = None
