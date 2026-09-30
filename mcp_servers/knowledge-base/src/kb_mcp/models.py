"""Typed contracts for tool outputs and the stitching of chunks back into text.

Rows from the database ignore extra fields so additive schema changes in the
ingestion pipeline don't break the server.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from kb_mcp.observability import current_trace_id

URI_SCHEME = "kb"

DocumentStatus = Literal["PENDING", "PARSING", "PROCESSING", "RATE_LIMITED_PAUSED", "COMPLETED", "FAILED"]
SearchMode = Literal["hybrid", "semantic", "keyword"]


def document_uri(document_id: str) -> str:
    return f"{URI_SCHEME}://documents/{document_id}"


def chunk_uri(document_id: str, chunk_index: int) -> str:
    return f"{URI_SCHEME}://documents/{document_id}/chunks/{chunk_index}"


# ---- Source records ---------------------------------------------------------


class Document(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    filename: str
    file_type: str
    file_size: int
    status: str
    total_chunks: int
    processed_chunks: int
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @computed_field(description="MCP resource URI for the full document text.")
    @property
    def resource_uri(self) -> str:
        return document_uri(self.id)


class Chunk(BaseModel):
    model_config = ConfigDict(extra="ignore")

    chunk_id: str
    document_id: str
    filename: str
    chunk_index: int
    page_number: int | None = None
    content: str

    @computed_field(description="MCP resource URI for this chunk.")
    @property
    def resource_uri(self) -> str:
        return chunk_uri(self.document_id, self.chunk_index)


class SearchHit(Chunk):
    score: float = Field(description="Ranking score for the chosen mode; higher is better.")
    similarity: float | None = Field(default=None, description="Cosine similarity (semantic/hybrid only).")
    keyword_rank: float | None = Field(default=None, description="Full-text rank (keyword/hybrid only).")


# ---- Tool results -----------------------------------------------------------


class ToolResult(BaseModel):
    trace_id: str = Field(default_factory=current_trace_id, description="Correlates this result with server logs.")


class SearchResults(ToolResult):
    query: str
    mode: SearchMode
    embedding_mode: str = Field(description="'gemini', or 'mock' when no API key is configured.")
    hits: list[SearchHit]


class DocumentList(ToolResult):
    documents: list[Document]
    total: int = Field(description="Total matches before limit/offset were applied.")


class DocumentPassage(ToolResult):
    document: Document
    first_chunk: int
    last_chunk: int
    text: str = Field(description="Chunks stitched together with the ingestion overlap removed.")
    chunks: list[Chunk]
    next_chunk: int | None = Field(description="Pass as `start_chunk` to continue reading; null at the end.")


class KnowledgeBaseStatus(ToolResult):
    documents_by_status: dict[str, int]
    total_documents: int
    total_chunks: int
    stored_embedding_dimension: int | None
    configured_embedding_dimension: int
    embedding_model: str
    embedding_mode: str
    warnings: list[str]


# ---- Text stitching ---------------------------------------------------------

# The ingestion chunker prefixes each chunk with ~150 chars from the end of the previous one.
MIN_OVERLAP = 20
MAX_OVERLAP_SCAN = 400


def overlap_length(previous: str, following: str) -> int:
    """Length of the longest suffix of `previous` that is also a prefix of `following`."""
    tail = previous[-MAX_OVERLAP_SCAN:]
    for size in range(min(len(tail), len(following)), MIN_OVERLAP - 1, -1):
        if tail.endswith(following[:size]):
            return size
    return 0


def stitch_chunks(chunks: list[Chunk], *, page_markers: bool = False) -> str:
    """Reassemble consecutive chunks into readable text, dropping the duplicated overlap.

    Chunks from different pages (or non-adjacent indexes) never overlap, so they
    are joined with a blank line instead.
    """
    parts: list[str] = []
    previous: Chunk | None = None
    for chunk in chunks:
        new_page = previous is None or chunk.page_number != previous.page_number
        if page_markers and new_page and chunk.page_number is not None:
            parts.append(f"\n\n<!-- page {chunk.page_number} -->\n\n")
        elif previous is not None:
            adjacent = chunk.chunk_index == previous.chunk_index + 1 and not new_page
            size = overlap_length(previous.content, chunk.content) if adjacent else 0
            if size:
                parts.append(chunk.content[size:])
                previous = chunk
                continue
            parts.append("\n\n")
        parts.append(chunk.content)
        previous = chunk
    return "".join(parts).strip()
