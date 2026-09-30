"""Pydantic v2 schemas for document ingestion and pgvector retrieval."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

DocumentStatus = Literal[
    "PENDING",
    "PARSING",
    "PROCESSING",
    "RATE_LIMITED_PAUSED",
    "COMPLETED",
    "FAILED",
]

DistanceMetric = Literal["cosine", "l2", "max_inner_product"]
SearchType = Literal["similarity", "keyword", "hybrid"]


class DocumentMetadata(BaseModel):
    """Metadata and processing status for an ingested document."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="Unique document UUID")
    filename: str = Field(description="Original filename")
    file_type: str = Field(description="Extension or format e.g. pdf, docx")
    file_size: int = Field(description="Size in bytes")
    file_path: str = Field(description="Path to stored file")
    status: DocumentStatus = Field(default="PENDING", description="Ingestion processing status")
    total_chunks: int = Field(default=0, description="Total chunks extracted")
    processed_chunks: int = Field(default=0, description="Number of vectorized chunks")
    progress_percent: float = Field(default=0.0, description="Percentage of chunks completed")
    error_message: Optional[str] = Field(default=None, description="Error details if failed")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ChunkRetrievalResult(BaseModel):
    """Enriched document chunk retrieved via vector, keyword, or hybrid search."""

    model_config = ConfigDict(extra="ignore")

    chunk_id: str = Field(description="Unique chunk UUID")
    document_id: str = Field(description="Parent document UUID")
    filename: str = Field(description="Parent document filename")
    file_type: str = Field(description="Parent document file type")
    file_path: Optional[str] = Field(default=None, description="Parent document storage path")
    document_status: Optional[str] = Field(default=None, description="Parent document status")
    chunk_index: int = Field(description="Zero-based sequence index within the document")
    page_number: Optional[int] = Field(default=None, description="Page number if applicable")
    content: str = Field(description="Text passage content")
    char_count: int = Field(description="Character count of chunk")
    estimated_tokens: int = Field(description="Estimated token count")
    similarity_score: float = Field(
        default=0.0,
        description="Normalized relevance score between 0.0 (no match) and 1.0 (exact match)",
    )
    distance: Optional[float] = Field(
        default=None,
        description="Raw vector distance metric (e.g. pgvector cosine distance)",
    )
    created_at: Optional[datetime] = None


class DocumentRetrievalResponse(BaseModel):
    """Response payload for document chunk search queries."""

    model_config = ConfigDict(extra="ignore")

    query: Optional[str] = Field(default=None, description="Text query if provided")
    results: List[ChunkRetrievalResult] = Field(
        default_factory=list,
        description="Ranked list of matching document chunks",
    )
    total_results: int = Field(default=0, description="Number of results returned")
    search_type: SearchType = Field(
        default="similarity",
        description="Type of search executed: similarity, keyword, or hybrid",
    )
    execution_time_ms: float = Field(
        default=0.0,
        description="Execution duration in milliseconds",
    )
    trace_id: Optional[str] = Field(default=None, description="Correlation / distributed trace ID")


class ChunkContextResponse(BaseModel):
    """Context window expansion around a target chunk."""

    model_config = ConfigDict(extra="ignore")

    target_chunk: ChunkRetrievalResult = Field(description="The primary retrieved chunk")
    preceding_chunks: List[ChunkRetrievalResult] = Field(
        default_factory=list,
        description="Surrounding chunks appearing immediately before the target",
    )
    following_chunks: List[ChunkRetrievalResult] = Field(
        default_factory=list,
        description="Surrounding chunks appearing immediately after the target",
    )
    expanded_content: str = Field(
        description="Combined text from preceding, target, and following chunks in reading order",
    )
    window_size: int = Field(default=1, description="Number of adjacent chunks requested on each side")
    trace_id: Optional[str] = Field(default=None, description="Correlation / distributed trace ID")


class DocumentListResponse(BaseModel):
    """Paginated list of ingested documents."""

    model_config = ConfigDict(extra="ignore")

    documents: List[DocumentMetadata] = Field(default_factory=list)
    total_count: int = Field(default=0)
    limit: int = Field(default=50)
    offset: int = Field(default=0)
    trace_id: Optional[str] = Field(default=None)


class KnowledgeCitation(BaseModel):
    """Authoritative document citation passage retrieved via RAG."""

    model_config = ConfigDict(extra="ignore")

    chunk_id: str = Field(description="Unique chunk UUID or identifier")
    document_id: str = Field(description="Parent document UUID or identifier")
    filename: str = Field(description="Parent document filename (e.g. SOP-CMP-201-Discharge-Overpressure.md)")
    doc_type: str = Field(description="Document category or type description")
    section_title: Optional[str] = Field(default=None, description="Markdown section heading or procedure step")
    content: str = Field(description="Extracted or expanded technical text passage")
    similarity_score: float = Field(default=0.0, description="Normalized relevance score between 0.0 and 1.0")
    citation: str = Field(
        description="Standardized citation string, e.g. '[Source: SOP-CMP-201-Discharge-Overpressure.md, Section 2.1]'"
    )


class KnowledgeRetrievalResult(BaseModel):
    """Response payload for knowledge base RAG retrieval queries."""

    model_config = ConfigDict(extra="ignore")

    query: str = Field(description="User technical search query")
    doc_category: str = Field(description="Category filter applied: all, sop, troubleshooting_guide, etc.")
    citations: List[KnowledgeCitation] = Field(
        default_factory=list,
        description="Ranked list of authoritative document citations",
    )
    total_found: int = Field(default=0, description="Total matching passages found")
    execution_time_ms: float = Field(default=0.0, description="Execution duration in milliseconds")
    trace_id: Optional[str] = Field(default=None, description="Distributed trace identifier")
