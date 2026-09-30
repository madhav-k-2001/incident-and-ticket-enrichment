"""Use cases exposed as MCP tools and resources.

Composes repository queries into task-oriented results (hybrid ranking, passage
reading, document rendering). Knows nothing about MCP or SQL.
"""

import logging
from typing import Any

from pydantic import BaseModel, ValidationError

from kb_mcp.config import Settings
from kb_mcp.embedder import QueryEmbedder
from kb_mcp.errors import InvalidRequestError, NotFoundError, UnavailableError
from kb_mcp.models import (
    Chunk,
    Document,
    DocumentList,
    DocumentPassage,
    DocumentStatus,
    KnowledgeBaseStatus,
    SearchHit,
    SearchMode,
    SearchResults,
    stitch_chunks,
)
from kb_mcp.repository import Repository, Row

logger = logging.getLogger(__name__)

# Reciprocal Rank Fusion constant; 60 is the value from the original RRF paper.
RRF_K = 60
# Hybrid mode pulls this many times `top_k` candidates from each ranker before fusing.
HYBRID_CANDIDATE_FACTOR = 3
MAX_CANDIDATES = 100
# Cap on documents advertised in resources/list.
MAX_LISTED_RESOURCES = 500


class KnowledgeBaseService:
    def __init__(self, repository: Repository, embedder: QueryEmbedder, settings: Settings) -> None:
        self._repo = repository
        self._embedder = embedder
        self._settings = settings

    @property
    def embedding_mode(self) -> str:
        return self._embedder.mode

    # ---- Search -------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        mode: SearchMode = "hybrid",
        top_k: int = 5,
        document_ids: list[str] | None = None,
        filename_contains: str | None = None,
        min_similarity: float | None = None,
    ) -> SearchResults:
        filters: dict[str, Any] = {"document_ids": document_ids, "filename_contains": filename_contains}
        candidates = top_k if mode != "hybrid" else min(top_k * HYBRID_CANDIDATE_FACTOR, MAX_CANDIDATES)

        semantic: list[Row] = []
        keyword: list[Row] = []
        if mode in ("semantic", "hybrid"):
            vector = await self._embedder.embed_query(query)
            if len(vector) != self._settings.embedding_dimension:
                raise InvalidRequestError(
                    f"Query embedding has {len(vector)} dimensions; EMBEDDING_DIMENSION is "
                    f"{self._settings.embedding_dimension}."
                )
            semantic = await self._repo.semantic_search(vector, limit=candidates, **filters)
            if min_similarity is not None:
                semantic = [r for r in semantic if r["similarity"] >= min_similarity]
        if mode in ("keyword", "hybrid"):
            keyword = await self._repo.keyword_search(query, limit=candidates, **filters)

        if mode == "semantic":
            hits = [_hit(r, score=r["similarity"]) for r in semantic]
        elif mode == "keyword":
            hits = [_hit(r, score=r["keyword_rank"]) for r in keyword]
        else:
            hits = _fuse(semantic, keyword)[:top_k]

        logger.info("kb_search", extra={"fields": {"mode": mode, "hits": len(hits), "embedding": self.embedding_mode}})
        return SearchResults(query=query, mode=mode, embedding_mode=self.embedding_mode, hits=hits)

    # ---- Documents ----------------------------------------------------------

    async def list_documents(
        self,
        *,
        status: DocumentStatus | None = None,
        filename_contains: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> DocumentList:
        rows, total = await self._repo.list_documents(
            status=status, filename_contains=filename_contains, limit=limit, offset=offset
        )
        return DocumentList(documents=[_parse(Document, r) for r in rows], total=total)

    async def get_document(self, document_id: str) -> Document:
        row = await self._repo.get_document(document_id)
        if row is None:
            raise NotFoundError(f"Document {document_id} not found in the knowledge base.")
        return _parse(Document, row)

    async def read_document(self, document_id: str, *, start_chunk: int = 0, max_chunks: int = 20) -> DocumentPassage:
        document = await self.get_document(document_id)
        # Fetch one extra chunk to learn whether more follow, without a count query.
        rows = await self._repo.get_chunks(document_id, first=start_chunk, last=start_chunk + max_chunks)
        chunks = [_parse(Chunk, r) for r in rows]
        has_more = len(chunks) > max_chunks
        chunks = chunks[:max_chunks]
        if not chunks and start_chunk > 0:
            raise InvalidRequestError(f"Document {document_id} has no chunks from index {start_chunk} onwards.")
        return _passage(document, chunks, next_chunk=chunks[-1].chunk_index + 1 if has_more else None)

    async def get_chunk_context(self, document_id: str, chunk_index: int, *, window: int = 1) -> DocumentPassage:
        document = await self.get_document(document_id)
        rows = await self._repo.get_chunks(document_id, first=max(chunk_index - window, 0), last=chunk_index + window)
        chunks = [_parse(Chunk, r) for r in rows]
        if not any(c.chunk_index == chunk_index for c in chunks):
            raise NotFoundError(f"Chunk {chunk_index} of document {document_id} not found.")
        following = chunks[-1].chunk_index + 1
        return _passage(document, chunks, next_chunk=following if following < document.total_chunks else None)

    # ---- Resources ----------------------------------------------------------

    async def searchable_documents(self) -> list[Document]:
        """All fully ingested documents, for resource listing."""
        rows, _ = await self._repo.list_documents(
            status="COMPLETED", filename_contains=None, limit=MAX_LISTED_RESOURCES, offset=0
        )
        return [_parse(Document, r) for r in rows]

    async def document_catalog_json(self) -> str:
        documents = await self.list_documents(limit=1000)
        return documents.model_dump_json(indent=2, exclude={"trace_id"})

    async def document_markdown(self, document_id: str) -> str:
        document = await self.get_document(document_id)
        limit = self._settings.max_document_chunks
        rows = await self._repo.get_chunks(document_id, first=0, last=limit - 1)
        chunks = [_parse(Chunk, r) for r in rows]
        pages = {c.page_number for c in chunks if c.page_number is not None}
        body = stitch_chunks(chunks, page_markers=len(pages) > 1)

        header = [
            f"# {document.filename}",
            "",
            f"- Document ID: `{document.id}`",
            f"- Type: {document.file_type} | Status: {document.status} | "
            f"Chunks: {document.processed_chunks}/{document.total_chunks}",
        ]
        if document.updated_at:
            header.append(f"- Last updated: {document.updated_at.isoformat()}")
        if document.total_chunks > limit:
            header.append(f"- Note: truncated to the first {limit} chunks; use the `read_document` tool for the rest.")
        if document.status != "COMPLETED":
            header.append(f"- Note: ingestion is not complete ({document.status}); text may be partial.")
        return "\n".join(header) + "\n\n---\n\n" + (body or "_No text has been ingested for this document yet._") + "\n"

    async def chunk_text(self, document_id: str, chunk_index: int) -> str:
        rows = await self._repo.get_chunks(document_id, first=chunk_index, last=chunk_index)
        if not rows:
            raise NotFoundError(f"Chunk {chunk_index} of document {document_id} not found.")
        return _parse(Chunk, rows[0]).content

    # ---- Health -------------------------------------------------------------

    async def status(self) -> KnowledgeBaseStatus:
        stats = await self._repo.stats()
        by_status: dict[str, int] = stats["documents_by_status"]
        stored = stats["stored_embedding_dimension"]
        configured = self._settings.embedding_dimension

        warnings: list[str] = []
        if stored is not None and stored != configured:
            warnings.append(
                f"Stored vectors have {stored} dimensions but EMBEDDING_DIMENSION is {configured}; semantic search will fail."
            )
        if self.embedding_mode == "mock":
            warnings.append(
                "GEMINI_API_KEY is not set: queries use mock embeddings, so semantic ranking is only meaningful "
                "if the ingestion worker also ran in mock mode. Prefer mode='keyword' or 'hybrid'."
            )
        pending = sum(n for s, n in by_status.items() if s != "COMPLETED")
        if pending:
            warnings.append(f"{pending} document(s) are not fully ingested yet.")

        return KnowledgeBaseStatus(
            documents_by_status=by_status,
            total_documents=sum(by_status.values()),
            total_chunks=stats["total_chunks"],
            stored_embedding_dimension=stored,
            configured_embedding_dimension=configured,
            embedding_model=self._settings.gemini_embedding_model,
            embedding_mode=self.embedding_mode,
            warnings=warnings,
        )


# ---- Helpers ----------------------------------------------------------------


def _hit(row: Row, *, score: float) -> SearchHit:
    return _parse(SearchHit, {**row, "score": round(float(score), 6)})


def _fuse(semantic: list[Row], keyword: list[Row]) -> list[SearchHit]:
    """Reciprocal Rank Fusion: robust to the two rankers having incomparable score scales."""
    fused: dict[str, dict[str, Any]] = {}
    for ranked, field in ((semantic, "similarity"), (keyword, "keyword_rank")):
        for rank, row in enumerate(ranked, start=1):
            entry = fused.setdefault(row["chunk_id"], {**row, "score": 0.0})
            entry[field] = row[field]
            entry["score"] += 1.0 / (RRF_K + rank)
    ordered = sorted(fused.values(), key=lambda r: r["score"], reverse=True)
    return [_hit(r, score=r["score"]) for r in ordered]


def _passage(document: Document, chunks: list[Chunk], *, next_chunk: int | None) -> DocumentPassage:
    return DocumentPassage(
        document=document,
        first_chunk=chunks[0].chunk_index if chunks else 0,
        last_chunk=chunks[-1].chunk_index if chunks else 0,
        text=stitch_chunks(chunks),
        chunks=chunks,
        next_chunk=next_chunk,
    )


def _parse[M: BaseModel](model: type[M], data: Any) -> M:
    """Validate a database row; schema drift surfaces as a clear domain error."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        fields = {"model": model.__name__, "errors": exc.errors(include_input=False)}
        logger.error("unexpected_row_shape", extra={"fields": fields})
        raise UnavailableError(
            f"The knowledge base returned data that does not match the expected {model.__name__} shape."
        ) from exc
