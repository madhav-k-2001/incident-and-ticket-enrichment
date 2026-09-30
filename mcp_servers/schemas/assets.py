"""Pydantic schemas for Assets and Asset resolution."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class AssetSummary(BaseModel):
    """Compact summary of an asset for ambiguous match presentation."""

    model_config = ConfigDict(extra="ignore")

    asset_id: str
    asset_name: str
    site: Optional[str] = None
    unit: Optional[str] = None
    type: Optional[str] = None
    criticality: Optional[str] = None


class AssetMetadata(BaseModel):
    """Full asset details matching simulator data store."""

    model_config = ConfigDict(extra="allow")

    asset_id: str
    asset_name: str
    site: str
    unit: str
    type: str
    criticality: str
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    install_date: Optional[str] = None
    related_assets: List[str] = Field(default_factory=list)
    specifications: Dict[str, Any] = Field(default_factory=dict)
    maintenance_history_count: Optional[int] = None
    last_overhaul: Optional[str] = None


class AssetSearchResponse(BaseModel):
    """Raw simulator response from GET /assets/search."""

    model_config = ConfigDict(extra="ignore")

    results: List[AssetMetadata] = Field(default_factory=list)
    total: int = 0


class AssetResolutionResult(BaseModel):
    """
    Composite resolution result for resolve_asset.
    - status="resolved": exactly one asset matched or top match is unambiguous.
    - status="ambiguous": multiple non-exact matches found.
    - status="not_found": no assets found matching criteria.
    """

    model_config = ConfigDict(extra="ignore")

    status: Literal["resolved", "ambiguous", "not_found"]
    matched_asset: Optional[AssetMetadata] = None
    candidate_assets: List[AssetSummary] = Field(default_factory=list)
    message: str
    trace_id: Optional[str] = None
