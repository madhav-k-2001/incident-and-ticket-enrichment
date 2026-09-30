"""Pydantic schemas for Alarm Analytics, Correlations, History, and Hygiene."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from mcp_servers.schemas.common import TraceMetadata


class AlarmTrendDataPoint(BaseModel):
    timestamp: str
    alarm_count: int
    avg_ack_delay: float


class AlarmTrendsResponse(BaseModel):
    asset_ids: List[str] = Field(default_factory=list)
    bucket: str = "daily"
    time_range: Dict[str, Any] = Field(default_factory=dict)
    data: List[AlarmTrendDataPoint] = Field(default_factory=list)


class AlarmCorrelationItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_asset: str
    target_asset: str
    source_alarm_code: str
    target_alarm_code: str
    correlation_coefficient: float
    avg_lag_minutes: float
    cooccurrence_count: int
    significance: str
    description: str


class AlarmCorrelationResponse(BaseModel):
    correlation_method: str = "cooccurrence"
    lag_window_minutes: int = 15
    correlations: List[AlarmCorrelationItem] = Field(default_factory=list)
    trace_metadata: Optional[TraceMetadata] = None


class CorrelatedAlarmsResult(BaseModel):
    """
    Composite result for AlarmAnalyticsService.get_correlations.
    Directly extracts correlated_asset_ids excluding the queried assets for easy chaining.
    """

    model_config = ConfigDict(extra="ignore")

    queried_asset_ids: List[str] = Field(default_factory=list)
    correlated_asset_ids: List[str] = Field(default_factory=list)
    correlations: List[AlarmCorrelationItem] = Field(default_factory=list)
    trace_id: Optional[str] = None


class FloodWindow(BaseModel):
    model_config = ConfigDict(extra="allow")

    start: str
    end: str
    alarm_count: int
    peak_rate_per_min: float
    primary_contributing_assets: List[str] = Field(default_factory=list)


class FloodAnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    unit: str
    threshold_count: int
    rolling_window_minutes: int
    flood_events_count: int
    flood_windows: List[FloodWindow] = Field(default_factory=list)


class RationalizationCandidate(BaseModel):
    model_config = ConfigDict(extra="allow")

    alarm_id: str
    asset_id: str
    alarm_code: str
    alarm_name: str
    classification: str
    recurrence_count: int
    stale_duration_minutes: int
    justification: str
    recommended_action: str


class RationalizationResponse(BaseModel):
    candidates: List[RationalizationCandidate] = Field(default_factory=list)


class AlarmHygieneReport(BaseModel):
    """
    Composite report combining flood analysis and rationalization candidates.
    Built last, runs in parallel with partial failure resilience.
    """

    model_config = ConfigDict(extra="ignore")

    unit: Optional[str] = None
    flood_events_count: int = 0
    flood_windows: List[FloodWindow] = Field(default_factory=list)
    rationalization_candidates: List[RationalizationCandidate] = Field(default_factory=list)
    partial_errors: Dict[str, str] = Field(default_factory=dict)
    trace_id: Optional[str] = None


class RecurringAlarmStat(BaseModel):
    alarm_code: str
    alarm_name: str
    count: int
    severity: str


class DailyCount(BaseModel):
    date: str
    count: int


class AlarmHistoryAnalysis(BaseModel):
    """
    Composite analysis of historical alarms computed dynamically from actual records.
    Provides accurate recurrence rate and daily buckets over evaluated N days.
    """

    model_config = ConfigDict(extra="ignore")

    asset_id: str
    evaluation_period_days: int
    start_time: str
    end_time: str
    total_occurrences: int
    recurring_rate: float
    severity_breakdown: Dict[str, int] = Field(default_factory=dict)
    top_recurring_alarms: List[RecurringAlarmStat] = Field(default_factory=list)
    daily_trend: List[DailyCount] = Field(default_factory=list)
    trace_id: Optional[str] = None


class KPIDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    kpi_id: str
    name: str
    standard: str
    target: str
    description: str


class KPIDefinitionsResponse(BaseModel):
    kpis: List[KPIDefinition] = Field(default_factory=list)
