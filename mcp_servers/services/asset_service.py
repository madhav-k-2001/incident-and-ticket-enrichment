"""AssetService for resolving assets with ambiguity detection and metadata enrichment."""

from __future__ import annotations

import re
import uuid
from typing import List, Optional

from mcp_servers.client import BaseSimulatorClient
from mcp_servers.exceptions import NotFoundError
from mcp_servers.schemas.assets import (
    AssetMetadata,
    AssetResolutionResult,
    AssetSearchResponse,
    AssetSummary,
)


class AssetService:
    """Service for interacting with asset inventory and resolving assets for operators."""

    def __init__(self, client: BaseSimulatorClient) -> None:
        self.client = client

    async def resolve_asset(
        self,
        query: Optional[str] = None,
        unit: Optional[str] = None,
        site: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> AssetResolutionResult:
        """
        Search for an asset and retrieve full metadata for the best match.
        
        Resolution logic (Amendment 4):
        - Exact case-insensitive match on asset_id or asset_name, or a single result => resolved.
        - Multiple non-exact matches => ambiguous (returns candidate summaries).
        - Zero matches => retries with normalized text and individual tokens before returning not_found.
        - Query is optional (can filter by unit and site alone).
        - Generates one trace_id per composite call, passes to sub-calls, and includes in result.
        """
        tid = trace_id or f"trace-{uuid.uuid4().hex[:8]}"

        # Step 1: Initial search
        search_resp = await self._search_assets(
            query=query, unit=unit, site=site, limit=10, trace_id=tid
        )
        results = search_resp.results

        # Step 2: Retry with normalized tokens if 0 matches and query was provided
        if not results and query:
            # Normalize and split into meaningful alphanumeric tokens
            tokens = [t for t in re.split(r"[\s\-_/]+", query.strip()) if len(t) > 1]
            seen_ids = set()
            for token in tokens:
                token_resp = await self._search_assets(
                    query=token, unit=unit, site=site, limit=10, trace_id=tid
                )
                for a in token_resp.results:
                    if a.asset_id not in seen_ids:
                        seen_ids.add(a.asset_id)
                        results.append(a)
                if results:
                    break

        if not results:
            return AssetResolutionResult(
                status="not_found",
                matched_asset=None,
                candidate_assets=[],
                message=f"No asset found matching criteria: query={query!r}, unit={unit!r}, site={site!r}",
                trace_id=tid,
            )

        # Step 3: Check for exact match on asset_id or asset_name
        q_norm = query.strip().lower() if query else None
        exact_match: Optional[AssetMetadata] = None

        if q_norm:
            for a in results:
                if a.asset_id.lower() == q_norm or a.asset_name.lower() == q_norm:
                    exact_match = a
                    break

        # Single result or exact match => resolved
        if exact_match or len(results) == 1:
            matched = exact_match or results[0]
            # Fetch full metadata to ensure all specifications and latest status are loaded
            try:
                full_metadata = await self._get_asset_metadata(matched.asset_id, trace_id=tid)
            except NotFoundError:
                full_metadata = matched

            return AssetResolutionResult(
                status="resolved",
                matched_asset=full_metadata,
                candidate_assets=[
                    AssetSummary(
                        asset_id=matched.asset_id,
                        asset_name=matched.asset_name,
                        site=matched.site,
                        unit=matched.unit,
                        type=matched.type,
                        criticality=matched.criticality,
                    )
                ],
                message=f"Asset successfully resolved to {matched.asset_id} ({matched.asset_name})",
                trace_id=tid,
            )

        # Multiple non-exact matches => ambiguous
        candidates = [
            AssetSummary(
                asset_id=a.asset_id,
                asset_name=a.asset_name,
                site=a.site,
                unit=a.unit,
                type=a.type,
                criticality=a.criticality,
            )
            for a in results
        ]
        return AssetResolutionResult(
            status="ambiguous",
            matched_asset=None,
            candidate_assets=candidates,
            message=(
                f"Multiple candidate assets found matching {query!r} ({len(candidates)} candidates). "
                "Please specify unit, site, or exact asset_id."
            ),
            trace_id=tid,
        )

    # --------------------------------------------------------------------------
    # Private Helper Methods
    # --------------------------------------------------------------------------

    async def _search_assets(
        self,
        query: Optional[str] = None,
        unit: Optional[str] = None,
        site: Optional[str] = None,
        limit: int = 10,
        trace_id: Optional[str] = None,
    ) -> AssetSearchResponse:
        params = {}
        if query:
            params["query"] = query
        if unit:
            params["unit"] = unit
        if site:
            params["site"] = site
        params["limit"] = limit

        response = await self.client.request(
            method="GET",
            path="/assets/search",
            params=params,
            trace_id=trace_id,
        )
        return AssetSearchResponse.model_validate(response.json())

    async def _get_asset_metadata(
        self,
        asset_id: str,
        trace_id: Optional[str] = None,
    ) -> AssetMetadata:
        response = await self.client.request(
            method="GET",
            path=f"/assets/{asset_id}/metadata",
            trace_id=trace_id,
        )
        return AssetMetadata.model_validate(response.json())
