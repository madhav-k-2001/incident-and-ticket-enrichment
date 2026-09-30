"""MCP interface layer: tool definitions, input contracts and error mapping.

Tools stay thin: validate input (via type hints), bind a trace id, delegate to
AlarmService, and translate domain errors into MCP tool errors.
"""

import inspect
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from alarm_mcp.client import AlarmApiClient
from alarm_mcp.config import Settings
from alarm_mcp.errors import AlarmApiError
from alarm_mcp.models import (
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
from alarm_mcp.observability import bind_trace_id
from alarm_mcp.service import AlarmService

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
Read-only access to the plant Alarm Management system.

Typical workflow:
1. list_alarms - find alarms and pick the one to work on.
2. get_alarm_context - get everything needed to act on that alarm.
3. find_correlated_alarms / analyze_alarms - see the wider picture.

Use search_assets whenever you need to turn an asset name into an asset_id."""

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)

# ---- Shared parameter types -------------------------------------------------

Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")]
AssetId = Annotated[Identifier, Field(description="Exact asset id, e.g. 'CMP-201'.")]
AlarmId = Annotated[Identifier, Field(description="Exact alarm id, e.g. 'ALM-9021'.")]
AssetIds = Annotated[
    list[Identifier] | None,
    Field(max_length=50, description="Asset ids to include, e.g. ['CMP-201', 'M-501']. Omit for all assets."),
]
Site = Annotated[str | None, Field(max_length=100, description="Site name, e.g. 'EastRefinery'.")]
Unit = Annotated[str | None, Field(max_length=100, description="Process unit, e.g. 'Unit 2'.")]
StartTime = Annotated[
    datetime | None, Field(description="Start of the time window, ISO-8601 (treated as UTC if no offset).")
]
EndTime = Annotated[datetime | None, Field(description="End of the time window, ISO-8601 (treated as UTC if no offset).")]


def create_server(settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> MCPServer:
    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[AlarmService]:
        async with AlarmApiClient(settings, transport=transport) as api:
            yield AlarmService(api)

    mcp = MCPServer("alarm-management", version="0.1.0", instructions=INSTRUCTIONS, lifespan=lifespan)

    def tool[F: Callable[..., Any]](fn: F) -> F:
        """Register a read-only tool, using its dedented docstring as the description."""
        return mcp.tool(annotations=READ_ONLY, description=inspect.cleandoc(fn.__doc__ or ""))(fn)

    @tool
    async def search_assets(
        ctx: Context,
        query: Annotated[
            str | None, Field(max_length=100, description="Free text matched against asset name, id or type.")
        ] = None,
        asset_id: Annotated[
            Identifier | None, Field(description="Exact asset id. When set, the other filters are ignored.")
        ] = None,
        site: Site = None,
        unit: Unit = None,
        limit: Annotated[int, Field(ge=1, le=50, description="Maximum number of assets to return.")] = 10,
    ) -> AssetSearchResult:
        """Look up plant assets such as compressors, motors and pumps.

        Use this to turn an asset name into its asset_id, or to get an asset's details.
        - Exact lookup: pass asset_id.
        - Search: pass query and/or site/unit.

        Returns each asset's id, name, site, unit, type, criticality and related assets.
        """
        async with tool_call(ctx, "search_assets") as service:
            return await service.search_assets(query=query, asset_id=asset_id, site=site, unit=unit, limit=limit)

    @tool
    async def list_alarms(
        ctx: Context,
        asset_id: Annotated[Identifier | None, Field(description="Only alarms raised on this asset.")] = None,
        site: Site = None,
        unit: Unit = None,
        status: Annotated[AlarmStatus | None, Field(description="Only alarms in this state.")] = None,
        severity: Annotated[Severity | None, Field(description="Only alarms of this severity.")] = None,
        start_time: StartTime = None,
        end_time: EndTime = None,
        sort_by: Annotated[AlarmSortField, Field(description="Field to sort by.")] = "start_time",
        sort_order: Annotated[SortOrder, Field(description="'desc' = newest or highest first.")] = "desc",
        page: Annotated[int, Field(ge=1, description="Page number, starting at 1.")] = 1,
        page_size: Annotated[int, Field(ge=1, le=100, description="Alarms per page.")] = 20,
    ) -> AlarmPage:
        """List alarms, with optional filters, sorting and paging.

        Use this to find alarms or to pick the one to work on. For the most urgent
        open alarms, use status='active', sort_by='priority_score', sort_order='desc'.

        Returns one page of alarm records, plus the total count and number of pages.
        """
        async with tool_call(ctx, "list_alarms") as service:
            return await service.list_alarms(
                asset_id=asset_id,
                site=site,
                unit=unit,
                status=status,
                severity=severity,
                start_time=start_time,
                end_time=end_time,
                page=page,
                page_size=page_size,
                sort_by=sort_by,
                sort_order=sort_order,
            )

    @tool
    async def get_alarm_context(ctx: Context, alarm_id: AlarmId) -> AlarmContext:
        """Get everything needed to act on one alarm, in a single call.

        Returns:
        - alarm: the alarm record.
        - asset: details of the affected asset.
        - priority: priority score, urgency and recommended level (P1-P3).
        - recommendations: likely causes, immediate actions, safety precautions,
          related assets and historical insights.

        If asset, priority or recommendations can't be fetched, that part is null
        and the reason is listed in 'warnings'. The rest is still returned.
        """
        async with tool_call(ctx, "get_alarm_context") as service:
            return await service.get_alarm_context(alarm_id)

    @tool
    async def analyze_alarms(
        ctx: Context,
        asset_ids: AssetIds = None,
        severities: Annotated[
            list[Severity] | None, Field(description="Only count alarms of these severities. Omit for all.")
        ] = None,
        start_time: StartTime = None,
        end_time: EndTime = None,
        bucket: Annotated[TrendBucket, Field(description="Size of each trend interval.")] = "daily",
    ) -> AlarmAnalytics:
        """Summarise alarm activity for a set of assets over a time window.

        Use this for the big picture: how often alarms occur, which alarms occur
        most often, and whether alarm activity is rising.

        Returns the total alarm count, a count per severity, the most frequent
        alarms, KPIs (such as recurrence rate and average acknowledgement delay)
        and alarm counts over time. If the trend can't be fetched, 'trend' is
        empty and the reason is listed in 'warnings'.
        """
        async with tool_call(ctx, "analyze_alarms") as service:
            return await service.analyze_alarms(
                asset_ids=asset_ids, severities=severities, start_time=start_time, end_time=end_time, bucket=bucket
            )

    @tool
    async def find_correlated_alarms(
        ctx: Context,
        asset_ids: AssetIds = None,
        start_time: StartTime = None,
        end_time: EndTime = None,
        lag_window_minutes: Annotated[
            int, Field(ge=1, le=1440, description="Maximum time between two alarms for them to count as related.")
        ] = 15,
        severity_threshold: Annotated[Severity, Field(description="Ignore alarms below this severity.")] = "medium",
        min_support: Annotated[
            int, Field(ge=1, description="Minimum number of times a pair must occur together to be reported.")
        ] = 1,
    ) -> CorrelationResult:
        """Find alarms that tend to follow one another across assets.

        Example: a compressor overpressure alarm usually followed by a motor
        vibration alarm. Use this to trace an alarm to its likely upstream cause,
        or to see which other assets are likely to be affected next.

        Each result names the source and target assets and alarm codes, how
        strongly they are correlated, and the average delay between them.
        """
        async with tool_call(ctx, "find_correlated_alarms") as service:
            return await service.find_correlated_alarms(
                asset_ids=asset_ids,
                start_time=start_time,
                end_time=end_time,
                lag_window_minutes=lag_window_minutes,
                severity_threshold=severity_threshold,
                min_support=min_support,
            )

    return mcp


@asynccontextmanager
async def tool_call(ctx: Context, tool: str) -> AsyncIterator[AlarmService]:
    """Per-call plumbing: trace binding, timing/logging and error mapping."""
    trace_id = bind_trace_id(_incoming_trace_id(ctx))
    started = time.perf_counter()
    outcome = "ok"
    try:
        yield ctx.request_context.lifespan_context
    except AlarmApiError as exc:
        outcome = type(exc).__name__
        raise ToolError(f"{exc} (trace_id={trace_id})") from exc
    finally:
        fields = {"tool": tool, "outcome": outcome, "duration_ms": round((time.perf_counter() - started) * 1000, 1)}
        logger.info("tool_call", extra={"fields": fields})


def _incoming_trace_id(ctx: Context) -> str | None:
    """Honour a caller-supplied trace id from request _meta or HTTP headers."""
    meta = ctx.request_context.meta or {}
    headers = ctx.headers or {}
    trace_id = meta.get("trace_id") or headers.get("trace_id") or headers.get("x-trace-id")
    return str(trace_id)[:64] if trace_id else None
