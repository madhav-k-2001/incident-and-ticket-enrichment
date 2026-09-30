"""
Alarm MCP Server.

Exposes 7 operational tools for plant alarm triage, asset resolution,
context enrichment, recurrence analytics, correlation discovery,
alarm hygiene, and authoritative document RAG retrieval.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Literal, Optional, Sequence
import uuid

# FastMCP / MCPServer dual-compatibility layer
try:
    from mcp.server.fastmcp import FastMCP
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as FastMCP

from mcp_servers.client import BaseSimulatorClient
from mcp_servers.config import SimulatorConfig
from mcp_servers.schemas.alarms import (
    AlarmLookupResult,
    EnrichedAlarmContext,
)
from mcp_servers.schemas.analytics import (
    AlarmHistoryAnalysis,
    AlarmHygieneReport,
    CorrelatedAlarmsResult,
)
from mcp_servers.schemas.assets import AssetResolutionResult
from mcp_servers.schemas.common import AlarmSortBy, AlarmStatus, Severity, SortOrder
from mcp_servers.schemas.documents import (
    KnowledgeCitation,
    KnowledgeRetrievalResult,
)
from mcp_servers.services.alarm_analytics_service import AlarmAnalyticsService
from mcp_servers.services.alarm_service import AlarmService
from mcp_servers.services.asset_service import AssetService
from mcp_servers.services.document_retrieval_service import DocumentRetrievalService

logger = logging.getLogger("mcp_servers.alarm_server")

# Default paths for document corpus fallback
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RAG_DIR = PROJECT_ROOT / "test_data" / "rag_data"

DocCategoryType = Literal[
    "all",
    "sop",
    "troubleshooting_guide",
    "escalation_policy",
    "case_study",
    "safety",
]

DOC_TYPE_MAPPING = {
    "sop": "Standard Operating Procedure (SOP)",
    "troubleshooting_guide": "Technical Troubleshooting Guide (TSG)",
    "escalation_policy": "Governance & Escalation Policy",
    "case_study": "Support Knowledge Base (KB)",
    "safety": "Safety & Environmental (HSE)",
}

STOPWORDS = {
    "the", "a", "an", "is", "for", "to", "on", "in", "with", "and", "or", "of",
    "at", "by", "from", "up", "about", "into", "over", "after", "related",
    "problem", "issue", "find", "search", "show", "get", "please", "can",
}


def _classify_doc_category(filename: str) -> str:
    """Classify document filename into one of the 5 plant knowledge categories."""
    fn = filename.upper()
    if fn.startswith("SOP-"):
        return "sop"
    elif fn.startswith("TSG-"):
        return "troubleshooting_guide"
    elif "ESCALATION" in fn or "GOV" in fn:
        return "escalation_policy"
    elif fn.startswith("KB-"):
        return "case_study"
    elif fn.startswith("SAF-"):
        return "safety"
    return "general"


def _extract_sections_from_markdown(content: str) -> List[Dict[str, str]]:
    """Parse markdown file content into structured sections based on headings."""
    lines = content.splitlines()
    sections: List[Dict[str, str]] = []
    current_title = "Overview"
    current_lines: List[str] = []

    for line in lines:
        if line.startswith("## ") or line.startswith("### "):
            if current_lines:
                sec_text = "\n".join(current_lines).strip()
                if sec_text:
                    sections.append({
                        "title": current_title,
                        "content": sec_text,
                    })
                current_lines = []
            current_title = line.lstrip("#").strip()
        else:
            current_lines.append(line)

    if current_lines:
        sec_text = "\n".join(current_lines).strip()
        if sec_text:
            sections.append({
                "title": current_title,
                "content": sec_text,
            })

    return sections


def _search_local_markdown_corpus(
    query: str,
    doc_category: DocCategoryType = "all",
    asset_id: Optional[str] = None,
    top_k: int = 3,
    rag_dir: Optional[Path] = None,
) -> List[KnowledgeCitation]:
    """
    Search the local markdown corpus in test_data/rag_data/ when database is offline.
    Matches technical query tokens across titles and content, applying category and asset filters.
    """
    corpus_dir = rag_dir or DEFAULT_RAG_DIR
    if not corpus_dir.exists():
        logger.warning("RAG corpus directory %s does not exist", corpus_dir)
        return []

    tokens = [w for w in re.split(r"[^\w\-]+", query.lower()) if w and w not in STOPWORDS and len(w) > 1]
    asset_norm = asset_id.strip().upper() if asset_id else None

    scored_citations: List[tuple[float, KnowledgeCitation]] = []

    for file_path in corpus_dir.glob("**/*.md"):
        if file_path.name == "DOCUMENT_MANIFEST.md":
            continue

        file_cat = _classify_doc_category(file_path.name)
        if doc_category != "all" and file_cat != doc_category:
            continue

        try:
            raw_text = file_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.debug("Failed to read %s: %s", file_path, e)
            continue

        # If asset_id filter specified, verify file relevance
        if asset_norm and (asset_norm not in raw_text.upper() and file_cat != "escalation_policy"):
            continue

        sections = _extract_sections_from_markdown(raw_text)
        doc_type_label = DOC_TYPE_MAPPING.get(file_cat, "Technical Documentation")

        for idx, sec in enumerate(sections):
            sec_title = sec["title"]
            sec_content = sec["content"]
            sec_full = f"{sec_title}\n{sec_content}".lower()

            score = 0.0
            if tokens:
                for token in tokens:
                    if token in sec_title.lower():
                        score += 3.0
                    if token in sec_content.lower():
                        score += 1.0
                score = min(1.0, score / max(1.0, len(tokens) * 2.5))
            else:
                score = 0.5

            if asset_norm and asset_norm in sec_full.upper():
                score = min(1.0, score + 0.2)

            if score > 0.05 or not tokens:
                citation_str = f"[Source: {file_path.name}, {sec_title}]"
                citation = KnowledgeCitation(
                    chunk_id=f"{file_path.stem}-sec-{idx+1:02d}",
                    document_id=f"doc-{file_path.stem}",
                    filename=file_path.name,
                    doc_type=doc_type_label,
                    section_title=sec_title,
                    content=sec_content,
                    similarity_score=round(score, 4),
                    citation=citation_str,
                )
                scored_citations.append((score, citation))

    # Sort descending by score
    scored_citations.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored_citations[:top_k]]


def create_alarm_server(
    client: Optional[BaseSimulatorClient] = None,
    doc_service: Optional[DocumentRetrievalService] = None,
    config: Optional[SimulatorConfig] = None,
    rag_dir: Optional[Path] = None,
) -> FastMCP:
    """
    Factory creating and configuring the alarm_mcp_server with all 7 tools.
    Allows injecting mock clients or services for automated unit testing.
    """
    sim_client = client or BaseSimulatorClient(config=config)
    asset_service = AssetService(sim_client)
    alarm_service = AlarmService(sim_client)
    alarm_analytics_service = AlarmAnalyticsService(sim_client)

    # Document retrieval service (backed by pgvector / PostgreSQL if available)
    document_retrieval_service = doc_service or DocumentRetrievalService()

    mcp = FastMCP(
        name="alarm_mcp_server",
        instructions=(
            "East Refinery Plant Alarm Management & Knowledge Base Server. "
            "Provides tools for asset inventory resolution, multi-filter alarm queries, "
            "comprehensive context enrichment, historical alarm recurrence analytics, "
            "equipment correlation discovery, alarm flood hygiene, and authoritative document RAG retrieval."
        ),
    )

    # --------------------------------------------------------------------------
    # Tool 1: resolve_asset
    # --------------------------------------------------------------------------
    @mcp.tool(
        name="resolve_asset",
        description=(
            "Search for an industrial asset by equipment tag or fuzzy name (e.g. 'CMP-201', 'Boiler Feed Pump'). "
            "Resolves equipment to unambiguous records with full engineering specifications, site/unit location, "
            "and criticality. Detects ambiguity and returns candidate summaries if multiple matches exist."
        ),
    )
    async def resolve_asset(
        query: Optional[str] = None,
        unit: Optional[str] = None,
        site: Optional[str] = None,
    ) -> AssetResolutionResult:
        """Resolve an asset by query, unit, and/or site."""
        return await asset_service.resolve_asset(query=query, unit=unit, site=site)

    # --------------------------------------------------------------------------
    # Tool 2: get_alarms
    # --------------------------------------------------------------------------
    @mcp.tool(
        name="get_alarms",
        description=(
            "Retrieve and filter operational alarms across plant equipment. "
            "Filter by alarm_id, asset_id, site, unit, status (active, acknowledged, cleared, shelved), "
            "severity (critical, high, medium, low), and UTC time range. "
            "Applies client-side time-window filtering and priority sorting. Returns total count and list of matching alarms."
        ),
    )
    async def get_alarms(
        alarm_id: Optional[str] = None,
        site: Optional[str] = None,
        unit: Optional[str] = None,
        asset_id: Optional[str] = None,
        status: Optional[AlarmStatus] = None,
        severity: Optional[Severity] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        sort_by: AlarmSortBy = "priority_score",
        sort_order: SortOrder = "desc",
        limit: int = 50,
    ) -> AlarmLookupResult:
        """Discover and filter alarms with pagination and sorting."""
        return await alarm_service.get_alarms(
            alarm_id=alarm_id,
            site=site,
            unit=unit,
            asset_id=asset_id,
            status=status,
            severity=severity,
            start_time=start_time,
            end_time=end_time,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
        )

    # --------------------------------------------------------------------------
    # Tool 3: enrich_alarm_context
    # --------------------------------------------------------------------------
    @mcp.tool(
        name="enrich_alarm_context",
        description=(
            "Perform composite context enrichment for a specific alarm. "
            "Validates alarm existence and concurrently gathers underlying asset engineering limits, "
            "calculated priority score evaluation, and recommended operator guidance. "
            "Identifies generic fallback guidance and isolates partial downstream errors."
        ),
    )
    async def enrich_alarm_context(
        alarm_id: str,
        include_asset: bool = True,
        include_priority: bool = True,
        include_recommendations: bool = True,
    ) -> EnrichedAlarmContext:
        """Enrich alarm with asset specs, priority score, and operator recommendations."""
        return await alarm_service.enrich_alarm_context(
            alarm_id=alarm_id,
            include_asset=include_asset,
            include_priority=include_priority,
            include_recommendations=include_recommendations,
        )

    # --------------------------------------------------------------------------
    # Tool 4: get_alarm_history
    # --------------------------------------------------------------------------
    @mcp.tool(
        name="get_alarm_history",
        description=(
            "Analyze historical alarm occurrences and recurrence patterns for a specific asset over a period (default 90 days). "
            "Calculates the exact recurrence rate, top recurring alarm codes with counts and severities, "
            "severity breakdown, and daily trend buckets from actual plant alarm telemetry."
        ),
    )
    async def get_alarm_history(
        asset_id: str,
        days: int = 90,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> AlarmHistoryAnalysis:
        """Retrieve recurrence and historical alarm trend analytics for an asset."""
        return await alarm_analytics_service.get_alarm_history(
            asset_id=asset_id,
            days=days,
            start_time=start_time,
            end_time=end_time,
        )

    # --------------------------------------------------------------------------
    # Tool 5: get_correlations
    # --------------------------------------------------------------------------
    @mcp.tool(
        name="get_correlations",
        description=(
            "Discover causal and statistical alarm co-occurrence correlations between plant equipment. "
            "Returns correlation coefficients (default threshold >= 0.8), time lag, and a distinct list of "
            "correlated asset IDs (excluding queried assets) ready for downstream ticket investigation."
        ),
    )
    async def get_correlations(
        asset_ids: Optional[List[str]] = None,
        min_correlation: float = 0.8,
    ) -> CorrelatedAlarmsResult:
        """Find inter-equipment alarm correlations."""
        return await alarm_analytics_service.get_correlations(
            asset_ids=asset_ids,
            min_correlation=min_correlation,
        )

    # --------------------------------------------------------------------------
    # Tool 6: analyze_alarm_hygiene
    # --------------------------------------------------------------------------
    @mcp.tool(
        name="analyze_alarm_hygiene",
        description=(
            "Evaluate plant alarm hygiene by concurrently analyzing alarm flood events "
            "(periods exceeding threshold count within a rolling window) and rationalization candidates "
            "(chattering, stale, or frequent recurring alarms) according to ISA-18.2 guidelines."
        ),
    )
    async def analyze_alarm_hygiene(
        unit: Optional[str] = "Unit 2",
        asset_ids: Optional[List[str]] = None,
        threshold_count: int = 10,
        rolling_window_minutes: int = 10,
    ) -> AlarmHygieneReport:
        """Analyze alarm flood bursts and rationalization candidates."""
        return await alarm_analytics_service.analyze_alarm_hygiene(
            unit=unit,
            asset_ids=asset_ids,
            threshold_count=threshold_count,
            rolling_window_minutes=rolling_window_minutes,
        )

    # --------------------------------------------------------------------------
    # Tool 7: search_knowledge_base
    # --------------------------------------------------------------------------
    @mcp.tool(
        name="search_knowledge_base",
        description=(
            "Search the plant technical document corpus for Standard Operating Procedures (SOPs), "
            "Technical Troubleshooting Guides (TSGs), plant escalation policies, historical incident case studies, "
            "and safety rules. Retrieves semantically and lexically relevant passages, optionally expands adjacent "
            "chunk context, and formats authoritative citations with exact section references."
        ),
    )
    async def search_knowledge_base(
        query: str,
        doc_category: DocCategoryType = "all",
        asset_id: Optional[str] = None,
        top_k: int = 3,
        expand_context: bool = True,
    ) -> KnowledgeRetrievalResult:
        """Search plant SOPs, TSGs, escalation matrices, and case studies with RAG grounding."""
        start_time = time.perf_counter()
        tid = f"trace-{uuid.uuid4().hex[:8]}"

        citations: List[KnowledgeCitation] = []

        # Step 1: Try database retrieval via DocumentRetrievalService if operational
        try:
            db_resp = await document_retrieval_service.keyword_search(
                query=query,
                top_k=top_k * 2,
                status="COMPLETED",
                trace_id=tid,
            )

            for chunk_res in db_resp.results:
                # Apply doc_category filter
                chunk_cat = _classify_doc_category(chunk_res.filename)
                if doc_category != "all" and chunk_cat != doc_category:
                    continue

                # Apply asset_id filter if specified
                if asset_id and asset_id.upper() not in chunk_res.content.upper():
                    continue

                content_to_use = chunk_res.content
                if expand_context:
                    try:
                        ctx = await document_retrieval_service.get_chunk_context(
                            chunk_id=chunk_res.chunk_id,
                            window_size=1,
                            trace_id=tid,
                        )
                        content_to_use = ctx.expanded_content
                    except Exception:
                        pass

                # Extract section title from heading if present
                sec_match = re.search(r"^(?:##|###)\s*(.+)$", chunk_res.content, re.MULTILINE)
                sec_title = sec_match.group(1).strip() if sec_match else f"Section {chunk_res.chunk_index + 1}"
                doc_type_label = DOC_TYPE_MAPPING.get(chunk_cat, "Technical Documentation")
                citation_str = f"[Source: {chunk_res.filename}, {sec_title}]"

                citations.append(
                    KnowledgeCitation(
                        chunk_id=chunk_res.chunk_id,
                        document_id=chunk_res.document_id,
                        filename=chunk_res.filename,
                        doc_type=doc_type_label,
                        section_title=sec_title,
                        content=content_to_use,
                        similarity_score=chunk_res.similarity_score,
                        citation=citation_str,
                    )
                )

                if len(citations) >= top_k:
                    break

        except Exception as e:
            logger.info("Database retrieval unvailable or returned error (%s). Falling back to local RAG corpus.", e)

        # Step 2: Fallback to local Markdown corpus if database returned no results
        if not citations:
            citations = _search_local_markdown_corpus(
                query=query,
                doc_category=doc_category,
                asset_id=asset_id,
                top_k=top_k,
                rag_dir=rag_dir,
            )

        exec_time = round((time.perf_counter() - start_time) * 1000, 2)
        return KnowledgeRetrievalResult(
            query=query,
            doc_category=doc_category,
            citations=citations,
            total_found=len(citations),
            execution_time_ms=exec_time,
            trace_id=tid,
        )

    return mcp


# Module-level instance for direct import or fastmcp CLI
mcp = create_alarm_server()
server = mcp


def main() -> None:
    """CLI entrypoint for running alarm_mcp_server."""
    parser = argparse.ArgumentParser(description="Plant Alarm MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="MCP transport to run (default: stdio)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host for SSE / HTTP transport")
    parser.add_argument("--port", type=int, default=8001, help="Port for SSE / HTTP transport (default: 8001)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    logger.info("Starting alarm_mcp_server on transport=%s port=%d", args.transport, args.port)

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    elif args.transport == "sse":
        asyncio.run(mcp.run_sse_async(host=args.host, port=args.port))
    elif args.transport == "streamable-http":
        asyncio.run(mcp.run_streamable_http_async(host=args.host, port=args.port))


if __name__ == "__main__":
    main()
