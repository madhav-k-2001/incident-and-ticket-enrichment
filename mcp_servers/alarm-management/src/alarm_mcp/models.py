"""Typed contracts for tool inputs and outputs.

Source records allow extra fields so additive API changes don't break the server,
while the fields the copilot relies on are declared and validated.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from alarm_mcp.observability import current_trace_id

Severity = Literal["critical", "high", "medium", "low"]
AlarmStatus = Literal["active", "acknowledged", "cleared"]
AlarmSortField = Literal["start_time", "priority_score", "severity"]
SortOrder = Literal["asc", "desc"]
TrendBucket = Literal["hourly", "daily", "weekly"]


class _Record(BaseModel):
    model_config = ConfigDict(extra="allow")


# ---- Source records ---------------------------------------------------------


class Asset(_Record):
    asset_id: str
    asset_name: str
    site: str
    unit: str
    type: str
    criticality: str
    related_assets: list[str] = []


class Alarm(_Record):
    alarm_id: str
    asset_id: str
    asset_name: str
    site: str
    unit: str
    alarm_code: str
    alarm_name: str
    severity: str
    status: str
    start_time: str
    end_time: str | None = None
    ack_time: str | None = None
    value: float | None = None
    threshold: float | None = None
    unit_of_measure: str | None = None
    priority_score: float | None = None


class Pagination(BaseModel):
    page: int
    page_size: int
    total_count: int
    total_pages: int


class PriorityAssessment(_Record):
    priority_score: float
    urgency: str
    recommended_priority_level: str
    factors: dict[str, Any] = {}


class OperatorRecommendations(_Record):
    likely_causes: list[str] = []
    immediate_actions: list[str] = []
    safety_precautions: list[str] = []
    related_assets: list[dict[str, Any]] = []
    historical_insights: str | None = None


class AlarmGroup(_Record):
    group_key: str
    alarm_count: int


class TrendPoint(_Record):
    timestamp: str


class Correlation(_Record):
    source_asset: str
    target_asset: str
    source_alarm_code: str
    target_alarm_code: str
    correlation_coefficient: float
    avg_lag_minutes: float | None = None
    significance: str | None = None
    description: str | None = None


# ---- Tool results -----------------------------------------------------------


class ToolResult(BaseModel):
    trace_id: str = Field(default_factory=current_trace_id, description="Correlates this result with server logs.")


class AssetSearchResult(ToolResult):
    assets: list[Asset]
    total: int


class AlarmPage(ToolResult):
    alarms: list[Alarm]
    pagination: Pagination


class AlarmContext(ToolResult):
    alarm: Alarm
    asset: Asset | None = Field(description="Metadata of the alarmed asset; null if unavailable.")
    priority: PriorityAssessment | None = Field(description="Priority scoring; null if unavailable.")
    recommendations: OperatorRecommendations | None = Field(description="Operator guidance; null if unavailable.")
    warnings: list[str] = Field(description="Enrichment steps that failed (partial result).")


class AlarmAnalytics(ToolResult):
    total_alarms: int
    severity_breakdown: dict[str, int]
    kpis: dict[str, float]
    top_alarms: list[AlarmGroup]
    trend_bucket: TrendBucket
    trend: list[TrendPoint]
    warnings: list[str] = Field(description="Analytics sections that failed (partial result).")


class CorrelationResult(ToolResult):
    correlations: list[Correlation]
