"""AlarmService for querying alarms and enriching operational alarm context."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import uuid
from typing import Any, Dict, List, Optional, Union

from mcp_servers.client import BaseSimulatorClient
from mcp_servers.exceptions import NotFoundError, SimulatorClientError
from mcp_servers.schemas.alarms import (
    Alarm,
    AlarmListResponse,
    AlarmLookupResult,
    AlarmSummaryRequest,
    AlarmSummaryResponse,
    EnrichedAlarmContext,
    OperatorRecommendationResponse,
    PriorityScoreResponse,
)
from mcp_servers.schemas.assets import AssetMetadata
from mcp_servers.schemas.common import (
    AlarmSortBy,
    AlarmStatus,
    Severity,
    SortOrder,
    TimeRange,
)

SEVERITY_WEIGHTS: Dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}


def _parse_utc_timestamp(ts: Union[str, datetime]) -> datetime:
    """Helper to parse string or datetime to UTC datetime."""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    ts_clean = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts_clean)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class AlarmService:
    """Service managing alarm lookup, filtering, prioritization, and context enrichment."""

    def __init__(self, client: BaseSimulatorClient) -> None:
        self.client = client

    async def get_alarms(
        self,
        alarm_id: Optional[str] = None,
        site: Optional[str] = None,
        unit: Optional[str] = None,
        asset_id: Optional[str] = None,
        status: Optional[AlarmStatus] = None,
        severity: Optional[Severity] = None,
        start_time: Optional[Union[str, datetime]] = None,
        end_time: Optional[Union[str, datetime]] = None,
        sort_by: AlarmSortBy = "priority_score",
        sort_order: SortOrder = "desc",
        limit: int = 50,
        trace_id: Optional[str] = None,
    ) -> AlarmLookupResult:
        """
        Unified alarm lookup:
        - If alarm_id is provided, retrieves the single alarm directly.
        - Otherwise, fetches all pages from the simulator, applies client-side
          time-window filtering (start_time/end_time) and sorting, and returns
          the requested slice with total_count (before limit) and returned_count.
        """
        tid = trace_id or f"trace-{uuid.uuid4().hex[:8]}"

        applied_filters: Dict[str, Any] = {
            "alarm_id": alarm_id,
            "site": site,
            "unit": unit,
            "asset_id": asset_id,
            "status": status,
            "severity": severity,
            "start_time": str(start_time) if start_time else None,
            "end_time": str(end_time) if end_time else None,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "limit": limit,
        }

        # 1. Single alarm lookup
        if alarm_id:
            alarm = await self._get_raw_alarm(alarm_id, trace_id=tid)
            return AlarmLookupResult(
                alarms=[alarm],
                total_count=1,
                returned_count=1,
                applied_filters=applied_filters,
                trace_id=tid,
            )

        # 2. Fetch all pages from simulator
        all_alarms: List[Alarm] = []
        page = 1
        page_size = 50

        while True:
            resp = await self._list_raw_alarms(
                asset_id=asset_id,
                site=site,
                unit=unit,
                status=status,
                severity=severity,
                page=page,
                page_size=page_size,
                sort_by="start_time",  # Use safe sort field on backend to prevent crash
                sort_order="desc",
                trace_id=tid,
            )
            all_alarms.extend(resp.data)

            if page >= resp.pagination.total_pages or not resp.data:
                break
            page += 1

        # 3. Client-side time-window filtering (ignored by simulator backend)
        filtered = all_alarms
        if start_time is not None:
            start_dt = _parse_utc_timestamp(start_time)
            filtered = [
                a for a in filtered
                if _parse_utc_timestamp(a.start_time) >= start_dt
            ]

        if end_time is not None:
            end_dt = _parse_utc_timestamp(end_time)
            filtered = [
                a for a in filtered
                if _parse_utc_timestamp(a.start_time) <= end_dt
            ]

        # 4. Client-side robust sorting
        reverse = (sort_order.lower() == "desc")

        def _sort_key(a: Alarm) -> Any:
            if sort_by == "priority_score":
                return a.priority_score if a.priority_score is not None else -1.0
            if sort_by == "severity":
                return SEVERITY_WEIGHTS.get(a.severity.lower(), 0)
            if sort_by == "start_time":
                return a.start_time or ""
            return getattr(a, sort_by, "") or ""

        sorted_alarms = sorted(filtered, key=_sort_key, reverse=reverse)

        total_count = len(sorted_alarms)
        paged_alarms = sorted_alarms[:limit]
        returned_count = len(paged_alarms)

        return AlarmLookupResult(
            alarms=paged_alarms,
            total_count=total_count,
            returned_count=returned_count,
            applied_filters=applied_filters,
            trace_id=tid,
        )

    async def enrich_alarm_context(
        self,
        alarm_id: str,
        include_asset: bool = True,
        include_priority: bool = True,
        include_recommendations: bool = True,
        trace_id: Optional[str] = None,
    ) -> EnrichedAlarmContext:
        """
        Composite context enrichment:
        1. Validates alarm existence first via GET /alarms/{alarm_id} (raises NotFoundError
           on non-existent alarms to prevent simulator synthetic fallback data).
        2. Concurrently fetches asset metadata, priority score, and recommendations.
        3. Catches SimulatorClientError on individual sub-calls, recording in partial_errors.
        4. Detects generic recommendation fallbacks (asset_id UNKNOWN) and sets
           operator_guidance_is_generic=True.
        """
        tid = trace_id or f"trace-{uuid.uuid4().hex[:8]}"

        # Step 1: Sequential validation
        alarm = await self._get_raw_alarm(alarm_id, trace_id=tid)

        # Step 2: Build parallel sub-calls
        tasks = []
        task_names = []

        if include_asset:
            tasks.append(self._get_asset_metadata(alarm.asset_id, trace_id=tid))
            task_names.append("asset")

        if include_priority:
            tasks.append(self._get_raw_priority_score(alarm_id, trace_id=tid))
            task_names.append("priority_evaluation")

        if include_recommendations:
            tasks.append(self._get_raw_recommendations(alarm_id, trace_id=tid))
            task_names.append("operator_guidance")

        results = await asyncio.gather(*tasks, return_exceptions=True)

        asset: Optional[AssetMetadata] = None
        priority_eval: Optional[PriorityScoreResponse] = None
        operator_guidance: Optional[OperatorRecommendationResponse] = None
        is_generic_guidance = False
        partial_errors: Dict[str, str] = {}

        for name, res in zip(task_names, results):
            if isinstance(res, Exception):
                if isinstance(res, SimulatorClientError):
                    partial_errors[name] = str(res)
                else:
                    # Unexpected programming / runtime error; re-raise
                    raise res
            else:
                if name == "asset":
                    asset = res
                elif name == "priority_evaluation":
                    priority_eval = res
                elif name == "operator_guidance":
                    operator_guidance = res
                    # Check for generic fallback (UNKNOWN asset or unmapped alarm)
                    if operator_guidance and (
                        operator_guidance.asset_id.upper() in ("UNKNOWN", "")
                        or operator_guidance.asset_id.upper() != alarm.asset_id.upper()
                    ):
                        is_generic_guidance = True

        return EnrichedAlarmContext(
            alarm=alarm,
            asset=asset,
            priority_evaluation=priority_eval,
            operator_guidance=operator_guidance,
            operator_guidance_is_generic=is_generic_guidance,
            partial_errors=partial_errors,
            trace_id=tid,
        )

    # --------------------------------------------------------------------------
    # Private Helper Methods
    # --------------------------------------------------------------------------

    async def _get_raw_alarm(self, alarm_id: str, trace_id: Optional[str] = None) -> Alarm:
        response = await self.client.request(
            method="GET",
            path=f"/alarms/{alarm_id}",
            trace_id=trace_id,
        )
        return Alarm.model_validate(response.json())

    async def _list_raw_alarms(
        self,
        asset_id: Optional[str] = None,
        site: Optional[str] = None,
        unit: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
        sort_by: str = "start_time",
        sort_order: str = "desc",
        trace_id: Optional[str] = None,
    ) -> AlarmListResponse:
        params: Dict[str, Any] = {
            "page": page,
            "page_size": page_size,
            "sort_by": sort_by,
            "sort_order": sort_order,
        }
        if asset_id:
            params["asset_id"] = asset_id
        if site:
            params["site"] = site
        if unit:
            params["unit"] = unit
        if status:
            params["status"] = status
        if severity:
            params["severity"] = severity

        response = await self.client.request(
            method="GET",
            path="/alarms",
            params=params,
            trace_id=trace_id,
        )
        return AlarmListResponse.model_validate(response.json())

    async def _get_asset_metadata(self, asset_id: str, trace_id: Optional[str] = None) -> AssetMetadata:
        response = await self.client.request(
            method="GET",
            path=f"/assets/{asset_id}/metadata",
            trace_id=trace_id,
        )
        return AssetMetadata.model_validate(response.json())

    async def _get_raw_priority_score(
        self, alarm_id: str, trace_id: Optional[str] = None
    ) -> PriorityScoreResponse:
        response = await self.client.request(
            method="POST",
            path="/alarms/priority-score",
            json={"alarm_id": alarm_id},
            trace_id=trace_id,
        )
        return PriorityScoreResponse.model_validate(response.json())

    async def _get_raw_recommendations(
        self,
        alarm_id: str,
        include_related: bool = True,
        include_asset_context: bool = True,
        include_historical_pattern: bool = True,
        trace_id: Optional[str] = None,
    ) -> OperatorRecommendationResponse:
        response = await self.client.request(
            method="POST",
            path="/recommendations/operator-actions",
            json={
                "alarm_id": alarm_id,
                "include_related": include_related,
                "include_asset_context": include_asset_context,
                "include_historical_pattern": include_historical_pattern,
            },
            trace_id=trace_id,
        )
        return OperatorRecommendationResponse.model_validate(response.json())
