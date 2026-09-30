"""AlarmAnalyticsService for correlation analysis, historical recurrence, and alarm hygiene."""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import uuid
from typing import Any, Callable, Dict, List, Optional, Union

from mcp_servers.client import BaseSimulatorClient
from mcp_servers.exceptions import SimulatorClientError
from mcp_servers.schemas.alarms import Alarm
from mcp_servers.schemas.analytics import (
    AlarmCorrelationItem,
    AlarmCorrelationResponse,
    AlarmHistoryAnalysis,
    AlarmHygieneReport,
    CorrelatedAlarmsResult,
    DailyCount,
    FloodAnalysisResponse,
    KPIDefinitionsResponse,
    RationalizationCandidate,
    RationalizationResponse,
    RecurringAlarmStat,
)
from mcp_servers.services.alarm_service import _parse_utc_timestamp


class AlarmAnalyticsService:
    """Service handling alarm correlations, historical trends, and flood/rationalization hygiene."""

    def __init__(
        self,
        client: BaseSimulatorClient,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def get_correlations(
        self,
        asset_ids: Optional[List[str]] = None,
        min_correlation: float = 0.8,
        trace_id: Optional[str] = None,
    ) -> CorrelatedAlarmsResult:
        """
        Discover correlated alarms between equipment.
        
        Enhancements (Amendment 7):
        - Default min_correlation set to 0.8 (matching 0.84-0.91 range in mock dataset).
        - Actively filters simulator results to requested asset_ids (simulator ignores them).
        - Excludes queried assets from correlated_asset_ids so they are ready for downstream
          ticket searches without self-referential queries.
        """
        tid = trace_id or f"trace-{uuid.uuid4().hex[:8]}"

        payload: Dict[str, Any] = {
            "asset_ids": asset_ids or [],
            "correlation_method": "cooccurrence",
            "lag_window_minutes": 15,
            "min_support": 1,
        }

        resp = await self._post_correlation(payload, trace_id=tid)

        queried_set = {aid.strip().upper() for aid in asset_ids} if asset_ids else set()
        filtered_correlations: List[AlarmCorrelationItem] = []
        correlated_assets_set = set()

        for c in resp.correlations:
            # Check coefficient threshold
            if c.correlation_coefficient < min_correlation:
                continue

            src = c.source_asset.strip().upper()
            tgt = c.target_asset.strip().upper()

            # Filter to queried assets if specified
            if queried_set:
                if src in queried_set or tgt in queried_set:
                    filtered_correlations.append(c)
                    if src not in queried_set:
                        correlated_assets_set.add(c.source_asset)
                    if tgt not in queried_set:
                        correlated_assets_set.add(c.target_asset)
            else:
                filtered_correlations.append(c)
                correlated_assets_set.add(c.source_asset)
                correlated_assets_set.add(c.target_asset)

        return CorrelatedAlarmsResult(
            queried_asset_ids=asset_ids or [],
            correlated_asset_ids=sorted(list(correlated_assets_set)),
            correlations=filtered_correlations,
            trace_id=tid,
        )

    async def get_alarm_history(
        self,
        asset_id: str,
        days: int = 90,
        start_time: Optional[Union[str, datetime]] = None,
        end_time: Optional[Union[str, datetime]] = None,
        trace_id: Optional[str] = None,
    ) -> AlarmHistoryAnalysis:
        """
        Analyze historical alarms and recurrence for an asset over N days.
        
        Enhancements (Amendments 5 & 8):
        - Uses injectable clock for current time reference.
        - Calculates real recurrence rates, counts, and daily trend buckets from actual
          alarm data rather than simulator canned endpoint values.
        """
        tid = trace_id or f"trace-{uuid.uuid4().hex[:8]}"

        # Resolve evaluation window
        now_dt = self.clock()
        if end_time is not None:
            end_dt = _parse_utc_timestamp(end_time)
        else:
            end_dt = now_dt

        if start_time is not None:
            start_dt = _parse_utc_timestamp(start_time)
        else:
            start_dt = end_dt - timedelta(days=days)

        # Retrieve raw alarms for asset
        # Fetch directly using internal helper
        all_alarms: List[Alarm] = []
        page = 1
        while True:
            resp = await self._list_alarms_for_asset(
                asset_id=asset_id,
                page=page,
                page_size=50,
                trace_id=tid,
            )
            all_alarms.extend(resp.get("data", []))
            pagination = resp.get("pagination", {})
            if page >= pagination.get("total_pages", 1) or not resp.get("data"):
                break
            page += 1

        # Client-side time-window filtering
        matching_alarms: List[Dict[str, Any]] = []
        for a in all_alarms:
            ts = a.get("start_time")
            if not ts:
                continue
            alarm_dt = _parse_utc_timestamp(ts)
            if start_dt <= alarm_dt <= end_dt:
                matching_alarms.append(a)

        total_occurrences = len(matching_alarms)

        # Frequency and recurrence calculation
        code_counts = Counter(a.get("alarm_code", "UNKNOWN") for a in matching_alarms)
        code_meta = {}
        for a in matching_alarms:
            code = a.get("alarm_code", "UNKNOWN")
            if code not in code_meta:
                code_meta[code] = {
                    "name": a.get("alarm_name", ""),
                    "severity": a.get("severity", "medium"),
                }

        top_recurring: List[RecurringAlarmStat] = [
            RecurringAlarmStat(
                alarm_code=code,
                alarm_name=code_meta[code]["name"],
                count=count,
                severity=code_meta[code]["severity"],
            )
            for code, count in code_counts.most_common()
        ]

        recurring_events_count = sum(cnt for cnt in code_counts.values() if cnt > 1)
        recurring_rate = (
            round(recurring_events_count / total_occurrences, 3)
            if total_occurrences > 0
            else 0.0
        )

        severity_breakdown: Dict[str, int] = {
            "critical": sum(1 for a in matching_alarms if a.get("severity") == "critical"),
            "high": sum(1 for a in matching_alarms if a.get("severity") == "high"),
            "medium": sum(1 for a in matching_alarms if a.get("severity") == "medium"),
            "low": sum(1 for a in matching_alarms if a.get("severity") == "low"),
        }

        # Daily aggregation
        daily_counts_dict: Dict[str, int] = defaultdict(int)
        for a in matching_alarms:
            day_str = a.get("start_time", "")[:10]
            if day_str:
                daily_counts_dict[day_str] += 1

        daily_trend = [
            DailyCount(date=d, count=c)
            for d, c in sorted(daily_counts_dict.items())
        ]

        return AlarmHistoryAnalysis(
            asset_id=asset_id,
            evaluation_period_days=days,
            start_time=start_dt.isoformat(),
            end_time=end_dt.isoformat(),
            total_occurrences=total_occurrences,
            recurring_rate=recurring_rate,
            severity_breakdown=severity_breakdown,
            top_recurring_alarms=top_recurring,
            daily_trend=daily_trend,
            trace_id=tid,
        )

    async def analyze_alarm_hygiene(
        self,
        unit: Optional[str] = "Unit 2",
        asset_ids: Optional[List[str]] = None,
        threshold_count: int = 10,
        rolling_window_minutes: int = 10,
        trace_id: Optional[str] = None,
    ) -> AlarmHygieneReport:
        """
        Evaluate flood events and rationalization candidates in parallel.
        (Built last as per user instructions).
        """
        tid = trace_id or f"trace-{uuid.uuid4().hex[:8]}"

        flood_payload = {
            "unit": unit or "Unit 2",
            "threshold_count": threshold_count,
            "rolling_window_minutes": rolling_window_minutes,
        }
        rat_payload = {
            "asset_ids": asset_ids or [],
            "recurrence_threshold": 5,
            "stale_minutes_threshold": 180,
        }

        tasks = [
            self._post_flood_analysis(flood_payload, trace_id=tid),
            self._post_rationalization(rat_payload, trace_id=tid),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        flood_res, rat_res = results
        flood_windows = []
        candidates = []
        partial_errors: Dict[str, str] = {}

        if isinstance(flood_res, Exception):
            if isinstance(flood_res, SimulatorClientError):
                partial_errors["flood_analysis"] = str(flood_res)
            else:
                raise flood_res
        else:
            flood_windows = flood_res.flood_windows

        if isinstance(rat_res, Exception):
            if isinstance(rat_res, SimulatorClientError):
                partial_errors["rationalization"] = str(rat_res)
            else:
                raise rat_res
        else:
            raw_candidates = rat_res.candidates
            # Post-filter rationalization candidates by asset_ids if passed
            if asset_ids:
                asset_set = {aid.strip().upper() for aid in asset_ids}
                candidates = [c for c in raw_candidates if c.asset_id.upper() in asset_set]
            else:
                candidates = raw_candidates

        return AlarmHygieneReport(
            unit=unit,
            flood_events_count=len(flood_windows),
            flood_windows=flood_windows,
            rationalization_candidates=candidates,
            partial_errors=partial_errors,
            trace_id=tid,
        )

    # --------------------------------------------------------------------------
    # Private Helper Methods
    # --------------------------------------------------------------------------

    async def _post_correlation(
        self, payload: Dict[str, Any], trace_id: Optional[str] = None
    ) -> AlarmCorrelationResponse:
        response = await self.client.request(
            method="POST",
            path="/alarms/correlation",
            json=payload,
            trace_id=trace_id,
        )
        return AlarmCorrelationResponse.model_validate(response.json())

    async def _post_flood_analysis(
        self, payload: Dict[str, Any], trace_id: Optional[str] = None
    ) -> FloodAnalysisResponse:
        response = await self.client.request(
            method="POST",
            path="/alarms/flood-analysis",
            json=payload,
            trace_id=trace_id,
        )
        return FloodAnalysisResponse.model_validate(response.json())

    async def _post_rationalization(
        self, payload: Dict[str, Any], trace_id: Optional[str] = None
    ) -> RationalizationResponse:
        response = await self.client.request(
            method="POST",
            path="/alarms/rationalization-candidates",
            json=payload,
            trace_id=trace_id,
        )
        return RationalizationResponse.model_validate(response.json())

    async def _list_alarms_for_asset(
        self,
        asset_id: str,
        page: int = 1,
        page_size: int = 50,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        response = await self.client.request(
            method="GET",
            path="/alarms",
            params={
                "asset_id": asset_id,
                "page": page,
                "page_size": page_size,
                "sort_by": "start_time",
                "sort_order": "desc",
            },
            trace_id=trace_id,
        )
        return response.json()
