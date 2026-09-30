"""Use cases exposed as MCP tools.

Composes raw API calls into task-oriented results (e.g. one call that returns an
alarm with its asset, priority and recommendations) and validates every payload
against the typed contracts. Knows nothing about MCP or HTTP.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from alarm_mcp.client import AlarmApiClient
from alarm_mcp.errors import AlarmApiError, InvalidRequestError, UnexpectedResponseError
from alarm_mcp.models import (
    Alarm,
    AlarmAnalytics,
    AlarmContext,
    AlarmPage,
    AlarmSortField,
    AlarmStatus,
    AssetSearchResult,
    CorrelationResult,
    Severity,
    SortOrder,
    TrendBucket,
)

logger = logging.getLogger(__name__)


class AlarmService:
    def __init__(self, api: AlarmApiClient) -> None:
        self._api = api

    async def search_assets(
        self,
        *,
        query: str | None = None,
        asset_id: str | None = None,
        site: str | None = None,
        unit: str | None = None,
        limit: int = 10,
    ) -> AssetSearchResult:
        if asset_id:
            asset = await self._api.get_asset(asset_id)
            return _parse(AssetSearchResult, {"assets": [asset], "total": 1})

        data = await self._api.search_assets(query=query, site=site, unit=unit, limit=limit)
        return _parse(AssetSearchResult, {"assets": data.get("results"), "total": data.get("total")})

    async def list_alarms(
        self,
        *,
        asset_id: str | None = None,
        site: str | None = None,
        unit: str | None = None,
        status: AlarmStatus | None = None,
        severity: Severity | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
        sort_by: AlarmSortField = "start_time",
        sort_order: SortOrder = "desc",
    ) -> AlarmPage:
        window = _time_range(start_time, end_time) or {}
        data = await self._api.list_alarms(
            asset_id=asset_id,
            site=site,
            unit=unit,
            status=status,
            severity=severity,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
            **window,
        )
        return _parse(AlarmPage, {"alarms": data.get("data"), "pagination": data.get("pagination")})

    async def get_alarm_context(self, alarm_id: str) -> AlarmContext:
        alarm = _parse(Alarm, await self._api.get_alarm(alarm_id))

        asset, priority, recommendations = await asyncio.gather(
            self._api.get_asset(alarm.asset_id),
            self._api.priority_score(alarm_id),
            self._api.operator_recommendations(alarm_id),
            return_exceptions=True,
        )
        warnings: list[str] = []
        return _parse(
            AlarmContext,
            {
                "alarm": alarm,
                "asset": _settle(asset, "asset metadata", warnings),
                "priority": _settle(priority, "priority score", warnings),
                "recommendations": _settle(recommendations, "operator recommendations", warnings),
                "warnings": warnings,
            },
        )

    async def analyze_alarms(
        self,
        *,
        asset_ids: list[str] | None = None,
        severities: list[Severity] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        bucket: TrendBucket = "daily",
    ) -> AlarmAnalytics:
        scope = {"asset_ids": asset_ids, "time_range": _time_range(start_time, end_time)}

        summary, trends = await asyncio.gather(
            self._api.alarm_summary({**scope, "severity": severities}),
            self._api.alarm_trends({**scope, "bucket": bucket}),
            return_exceptions=True,
        )
        if isinstance(summary, BaseException):
            raise summary  # the summary is the core of this result; trends are optional

        warnings: list[str] = []
        trend = _settle(trends, "trend data", warnings) or {}
        return _parse(
            AlarmAnalytics,
            {
                "total_alarms": summary.get("total_alarms"),
                "severity_breakdown": summary.get("severity_breakdown"),
                "kpis": summary.get("kpis"),
                "top_alarms": summary.get("summary", []),
                "trend_bucket": bucket,
                "trend": trend.get("data", []),
                "warnings": warnings,
            },
        )

    async def find_correlated_alarms(
        self,
        *,
        asset_ids: list[str] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        lag_window_minutes: int = 15,
        severity_threshold: Severity = "medium",
        min_support: int = 1,
    ) -> CorrelationResult:
        data = await self._api.alarm_correlation(
            {
                "asset_ids": asset_ids,
                "time_range": _time_range(start_time, end_time),
                "correlation_method": "cooccurrence",
                "lag_window_minutes": lag_window_minutes,
                "severity_threshold": severity_threshold,
                "min_support": min_support,
            }
        )
        return _parse(CorrelationResult, {"correlations": data.get("correlations")})


# ---- Helpers ----------------------------------------------------------------


def _parse[M: BaseModel](model: type[M], data: dict[str, Any]) -> M:
    """Validate an API-derived payload; schema drift surfaces as a clear domain error."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        fields = {"model": model.__name__, "errors": exc.errors(include_input=False)}
        logger.error("unexpected_api_response", extra={"fields": fields})
        raise UnexpectedResponseError(
            f"Alarm API returned data that does not match the expected {model.__name__} shape."
        ) from exc


def _settle(outcome: Any, label: str, warnings: list[str]) -> Any | None:
    """Turn an optional sub-call's API failure into a warning (partial result)."""
    if isinstance(outcome, AlarmApiError):
        warnings.append(f"{label} unavailable: {outcome}")
        return None
    if isinstance(outcome, BaseException):
        raise outcome
    return outcome


def _time_range(start: datetime | None, end: datetime | None) -> dict[str, str] | None:
    """Build the API's time_range object; naive datetimes are treated as UTC."""
    start, end = _as_utc(start), _as_utc(end)
    if start and end and start >= end:
        raise InvalidRequestError("start_time must be earlier than end_time.")

    window = {}
    if start:
        window["start_time"] = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    if end:
        window["end_time"] = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    return window or None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
