"""DocumentRetrievalService for querying pgvector embeddings and PostgreSQL document records."""

from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Literal, Optional, Sequence, Set, Tuple, Union
import uuid

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.base_service import BaseDatabaseService
from db.client import DatabaseClient
from db.config import get_settings
from db.models.document import Document, DocumentChunk
from mcp_servers.exceptions import NotFoundError, ValidationError
from mcp_servers.schemas.documents import (
    ChunkContextResponse,
    ChunkRetrievalResult,
    DistanceMetric,
    DocumentListResponse,
    DocumentMetadata,
    DocumentRetrievalResponse,
    DocumentStatus,
    SearchType,
)


class DocumentRetrievalService(BaseDatabaseService):
    """
    Asynchronous retrieval service for vectorized document passages stored in PostgreSQL via pgvector.
    
    Provides:
    - Cosine similarity vector search matching pgvector HNSW index ops.
    - Full-text & substring keyword search over document chunks.
    - Hybrid retrieval combining vector and lexical results via Reciprocal Rank Fusion (RRF).
    - Context window expansion (retrieving adjacent chunks for LLM context).
    - Document metadata lookup and ingestion progress status monitoring.
    """

    def __init__(
        self,
        db: Optional[Union[DatabaseClient, AsyncSession]] = None,
        embedding_dimension: Optional[int] = None,
    ) -> None:
        super().__init__(db=db)
        # Support duck-typed or mocked AsyncSession instances
        if db is not None and not isinstance(db, DatabaseClient):
            self._session = db
            self._client = None

        settings = get_settings()
        self.embedding_dimension: int = (
            embedding_dimension
            if embedding_dimension is not None
            else settings.EMBEDDING_DIMENSION
        )

    def _validate_vector(self, query_embedding: Sequence[float]) -> List[float]:
        """Validate query embedding vector format and dimension."""
        if not isinstance(query_embedding, (list, tuple)):
            raise ValidationError(
                f"query_embedding must be a sequence of floats, got {type(query_embedding).__name__}",
                status_code=400,
            )
        dim = len(query_embedding)
        if dim != self.embedding_dimension:
            raise ValidationError(
                f"Embedding dimension mismatch: expected {self.embedding_dimension}, received {dim}",
                status_code=400,
            )
        return [float(x) for x in query_embedding]

    @staticmethod
    def _chunk_to_result(
        chunk: DocumentChunk,
        doc: Document,
        similarity_score: float = 0.0,
        distance: Optional[float] = None,
    ) -> ChunkRetrievalResult:
        """Convert ORM DocumentChunk and parent Document into a typed retrieval result."""
        return ChunkRetrievalResult(
            chunk_id=chunk.id,
            document_id=doc.id,
            filename=doc.filename,
            file_type=doc.file_type,
            file_path=doc.file_path,
            document_status=doc.status,
            chunk_index=chunk.chunk_index,
            page_number=chunk.page_number,
            content=chunk.content,
            char_count=chunk.char_count,
            estimated_tokens=chunk.estimated_tokens,
            similarity_score=round(similarity_score, 4),
            distance=round(distance, 4) if distance is not None else None,
            created_at=chunk.created_at,
        )

    @staticmethod
    def _doc_to_metadata(doc: Document) -> DocumentMetadata:
        """Convert ORM Document into a typed DocumentMetadata schema."""
        progress = 0.0
        if doc.total_chunks > 0:
            progress = round((doc.processed_chunks / doc.total_chunks) * 100, 1)
        elif doc.status == "COMPLETED":
            progress = 100.0

        return DocumentMetadata(
            id=doc.id,
            filename=doc.filename,
            file_type=doc.file_type,
            file_size=doc.file_size,
            file_path=doc.file_path,
            status=doc.status,
            total_chunks=doc.total_chunks,
            processed_chunks=doc.processed_chunks,
            progress_percent=progress,
            error_message=doc.error_message,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )

    async def similarity_search(
        self,
        query_embedding: Sequence[float],
        top_k: int = 5,
        similarity_threshold: Optional[float] = None,
        document_ids: Optional[List[str]] = None,
        file_types: Optional[List[str]] = None,
        status: Optional[str] = "COMPLETED",
        distance_metric: DistanceMetric = "cosine",
        trace_id: Optional[str] = None,
    ) -> DocumentRetrievalResponse:
        """
        Execute vector similarity search across document chunks using pgvector distance operators.
        
        Args:
            query_embedding: Vector embedding matching the configured dimension.
            top_k: Maximum number of chunks to return.
            similarity_threshold: Optional minimum similarity score (0.0 to 1.0).
            document_ids: Optional filter restricting search to specific document IDs.
            file_types: Optional filter by file extensions (e.g. ['pdf', 'docx']).
            status: Document processing status filter (defaults to 'COMPLETED'; pass None for all).
            distance_metric: pgvector metric: 'cosine' (default), 'l2', or 'max_inner_product'.
            trace_id: Optional distributed trace identifier.
        """
        start_time = time.perf_counter()
        tid = trace_id or f"trace-{uuid.uuid4().hex[:8]}"
        vector = self._validate_vector(query_embedding)

        if distance_metric == "cosine":
            distance_col = DocumentChunk.embedding.cosine_distance(vector)

            def score_calc(d: float) -> float:
                return max(0.0, min(1.0, 1.0 - d))

        elif distance_metric == "l2":
            distance_col = DocumentChunk.embedding.l2_distance(vector)

            def score_calc(d: float) -> float:
                return 1.0 / (1.0 + d)

        elif distance_metric == "max_inner_product":
            distance_col = DocumentChunk.embedding.max_inner_product(vector)

            def score_calc(d: float) -> float:
                return float(-d)

        else:
            raise ValidationError(
                f"Unsupported distance metric '{distance_metric}'. Supported: 'cosine', 'l2', 'max_inner_product'.",
                status_code=400,
            )

        stmt = (
            select(DocumentChunk, Document, distance_col.label("distance"))
            .join(Document, DocumentChunk.document_id == Document.id)
        )

        if status is not None:
            stmt = stmt.where(Document.status == status)
        if document_ids:
            stmt = stmt.where(Document.id.in_(document_ids))
        if file_types:
            stmt = stmt.where(Document.file_type.in_(file_types))

        # Fetch extra candidates if filtering by threshold
        fetch_limit = top_k * 3 if similarity_threshold is not None else top_k
        stmt = stmt.order_by(distance_col.asc()).limit(fetch_limit)

        async with self.get_session() as session:
            rows = (await session.execute(stmt)).all()

        results: List[ChunkRetrievalResult] = []
        for chunk, doc, raw_dist in rows:
            dist_val = float(raw_dist) if raw_dist is not None else 0.0
            score = score_calc(dist_val)
            if similarity_threshold is not None and score < similarity_threshold:
                continue

            results.append(
                self._chunk_to_result(
                    chunk=chunk,
                    doc=doc,
                    similarity_score=score,
                    distance=dist_val,
                )
            )
            if len(results) >= top_k:
                break

        exec_time = round((time.perf_counter() - start_time) * 1000, 2)
        return DocumentRetrievalResponse(
            query=None,
            results=results,
            total_results=len(results),
            search_type="similarity",
            execution_time_ms=exec_time,
            trace_id=tid,
        )

    async def keyword_search(
        self,
        query: str,
        top_k: int = 5,
        document_ids: Optional[List[str]] = None,
        file_types: Optional[List[str]] = None,
        status: Optional[str] = "COMPLETED",
        trace_id: Optional[str] = None,
    ) -> DocumentRetrievalResponse:
        """
        Execute lexical / substring search across chunk text passages.
        
        Args:
            query: Keyword string or phrase to match.
            top_k: Maximum number of chunks to return.
            document_ids: Optional document ID filter.
            file_types: Optional file format filter.
            status: Document processing status filter (defaults to 'COMPLETED').
            trace_id: Optional distributed trace identifier.
        """
        start_time = time.perf_counter()
        tid = trace_id or f"trace-{uuid.uuid4().hex[:8]}"
        q_clean = query.strip()
        if not q_clean:
            return DocumentRetrievalResponse(
                query=query,
                results=[],
                total_results=0,
                search_type="keyword",
                execution_time_ms=round((time.perf_counter() - start_time) * 1000, 2),
                trace_id=tid,
            )

        stmt = (
            select(DocumentChunk, Document)
            .join(Document, DocumentChunk.document_id == Document.id)
            .where(DocumentChunk.content.ilike(f"%{q_clean}%"))
        )

        if status is not None:
            stmt = stmt.where(Document.status == status)
        if document_ids:
            stmt = stmt.where(Document.id.in_(document_ids))
        if file_types:
            stmt = stmt.where(Document.file_type.in_(file_types))

        stmt = stmt.order_by(DocumentChunk.chunk_index.asc()).limit(top_k)

        async with self.get_session() as session:
            rows = (await session.execute(stmt)).all()

        results: List[ChunkRetrievalResult] = []
        for idx, (chunk, doc) in enumerate(rows):
            # Assign rank-decayed score for lexical matches (1.0, 0.9, 0.8...)
            lexical_score = max(0.1, round(1.0 - (idx * 0.05), 2))
            results.append(
                self._chunk_to_result(
                    chunk=chunk,
                    doc=doc,
                    similarity_score=lexical_score,
                    distance=None,
                )
            )

        exec_time = round((time.perf_counter() - start_time) * 1000, 2)
        return DocumentRetrievalResponse(
            query=query,
            results=results,
            total_results=len(results),
            search_type="keyword",
            execution_time_ms=exec_time,
            trace_id=tid,
        )

    async def hybrid_search(
        self,
        query: str,
        query_embedding: Sequence[float],
        top_k: int = 5,
        alpha: float = 0.5,
        similarity_threshold: Optional[float] = None,
        document_ids: Optional[List[str]] = None,
        file_types: Optional[List[str]] = None,
        status: Optional[str] = "COMPLETED",
        distance_metric: DistanceMetric = "cosine",
        trace_id: Optional[str] = None,
    ) -> DocumentRetrievalResponse:
        """
        Combine vector semantic retrieval and keyword matching via Reciprocal Rank Fusion (RRF).
        
        Args:
            query: Lexical search string.
            query_embedding: Vector embedding for semantic search.
            top_k: Maximum combined results to return.
            alpha: Weighting factor (0.0 = pure vector, 1.0 = pure keyword, default 0.5 = balanced).
            similarity_threshold: Optional threshold for vector similarity.
            document_ids: Optional document ID filter.
            file_types: Optional file format filter.
            status: Document processing status filter (default 'COMPLETED').
            distance_metric: Distance metric to use for vector stage.
            trace_id: Optional distributed trace identifier.
        """
        start_time = time.perf_counter()
        tid = trace_id or f"trace-{uuid.uuid4().hex[:8]}"

        # Retrieve candidates from both modalities with an expanded pool
        candidate_k = max(top_k * 2, 10)
        vec_resp = await self.similarity_search(
            query_embedding=query_embedding,
            top_k=candidate_k,
            similarity_threshold=similarity_threshold,
            document_ids=document_ids,
            file_types=file_types,
            status=status,
            distance_metric=distance_metric,
            trace_id=tid,
        )

        keyword_resp = await self.keyword_search(
            query=query,
            top_k=candidate_k,
            document_ids=document_ids,
            file_types=file_types,
            status=status,
            trace_id=tid,
        )

        # Reciprocal Rank Fusion (RRF) with constant k=60
        rrf_k = 60.0
        scores: Dict[str, float] = {}
        chunks_by_id: Dict[str, ChunkRetrievalResult] = {}

        # 1. Score vector results
        vec_weight = 1.0 - alpha
        for rank, res in enumerate(vec_resp.results, start=1):
            chunks_by_id[res.chunk_id] = res
            scores[res.chunk_id] = scores.get(res.chunk_id, 0.0) + vec_weight * (1.0 / (rrf_k + rank))

        # 2. Score keyword results
        key_weight = alpha
        for rank, res in enumerate(keyword_resp.results, start=1):
            if res.chunk_id not in chunks_by_id:
                chunks_by_id[res.chunk_id] = res
            scores[res.chunk_id] = scores.get(res.chunk_id, 0.0) + key_weight * (1.0 / (rrf_k + rank))

        # Sort combined results by RRF score descending
        sorted_ids = sorted(scores.keys(), key=lambda cid: scores[cid], reverse=True)

        # Normalize top RRF score to 1.0
        max_rrf = max(scores.values()) if scores else 1.0
        final_results: List[ChunkRetrievalResult] = []

        for cid in sorted_ids[:top_k]:
            base_item = chunks_by_id[cid]
            normalized_score = round(scores[cid] / max_rrf, 4) if max_rrf > 0 else 0.0
            updated_item = base_item.model_copy(
                update={"similarity_score": normalized_score}
            )
            final_results.append(updated_item)

        exec_time = round((time.perf_counter() - start_time) * 1000, 2)
        return DocumentRetrievalResponse(
            query=query,
            results=final_results,
            total_results=len(final_results),
            search_type="hybrid",
            execution_time_ms=exec_time,
            trace_id=tid,
        )

    async def get_chunk_context(
        self,
        chunk_id: str,
        window_size: int = 1,
        trace_id: Optional[str] = None,
    ) -> ChunkContextResponse:
        """
        Retrieve a chunk along with adjacent preceding and following chunks for context window expansion.
        
        Args:
            chunk_id: UUID of the target chunk.
            window_size: Number of adjacent chunks to retrieve on each side.
            trace_id: Optional distributed trace identifier.
        """
        tid = trace_id or f"trace-{uuid.uuid4().hex[:8]}"

        async with self.get_session() as session:
            # 1. Fetch the target chunk and its parent document
            target_stmt = (
                select(DocumentChunk, Document)
                .join(Document, DocumentChunk.document_id == Document.id)
                .where(DocumentChunk.id == chunk_id)
            )
            row = (await session.execute(target_stmt)).first()
            if row is None:
                raise NotFoundError(
                    f"Document chunk '{chunk_id}' not found.",
                    status_code=404,
                )

            target_chunk, target_doc = row
            target_res = self._chunk_to_result(
                chunk=target_chunk,
                doc=target_doc,
                similarity_score=1.0,
            )

            # 2. Fetch preceding chunks
            preceding_stmt = (
                select(DocumentChunk, Document)
                .join(Document, DocumentChunk.document_id == Document.id)
                .where(
                    DocumentChunk.document_id == target_chunk.document_id,
                    DocumentChunk.chunk_index >= max(0, target_chunk.chunk_index - window_size),
                    DocumentChunk.chunk_index < target_chunk.chunk_index,
                )
                .order_by(DocumentChunk.chunk_index.asc())
            )
            prec_rows = (await session.execute(preceding_stmt)).all()
            preceding = [
                self._chunk_to_result(chunk=c, doc=d) for c, d in prec_rows
            ]

            # 3. Fetch following chunks
            following_stmt = (
                select(DocumentChunk, Document)
                .join(Document, DocumentChunk.document_id == Document.id)
                .where(
                    DocumentChunk.document_id == target_chunk.document_id,
                    DocumentChunk.chunk_index > target_chunk.chunk_index,
                    DocumentChunk.chunk_index <= target_chunk.chunk_index + window_size,
                )
                .order_by(DocumentChunk.chunk_index.asc())
            )
            foll_rows = (await session.execute(following_stmt)).all()
            following = [
                self._chunk_to_result(chunk=c, doc=d) for c, d in foll_rows
            ]

        # Combine text content in chronological reading order
        all_ordered = preceding + [target_res] + following
        combined = "\n\n".join(c.content.strip() for c in all_ordered if c.content.strip())

        return ChunkContextResponse(
            target_chunk=target_res,
            preceding_chunks=preceding,
            following_chunks=following,
            expanded_content=combined,
            window_size=window_size,
            trace_id=tid,
        )

    async def get_document(
        self,
        document_id: str,
        trace_id: Optional[str] = None,
    ) -> Optional[DocumentMetadata]:
        """Fetch document metadata and ingestion status by ID."""
        async with self.get_session() as session:
            stmt = select(Document).where(Document.id == document_id)
            doc = (await session.execute(stmt)).scalar_one_or_none()
            if doc is None:
                return None
            return self._doc_to_metadata(doc)

    async def list_documents(
        self,
        status: Optional[str] = None,
        file_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        trace_id: Optional[str] = None,
    ) -> DocumentListResponse:
        """List documents with optional filtering by status and file type."""
        tid = trace_id or f"trace-{uuid.uuid4().hex[:8]}"

        count_stmt = select(func.count(Document.id))
        stmt = select(Document)

        if status is not None:
            count_stmt = count_stmt.where(Document.status == status)
            stmt = stmt.where(Document.status == status)
        if file_type is not None:
            count_stmt = count_stmt.where(Document.file_type == file_type)
            stmt = stmt.where(Document.file_type == file_type)

        stmt = stmt.order_by(desc(Document.created_at)).limit(limit).offset(offset)

        async with self.get_session() as session:
            total_count = (await session.execute(count_stmt)).scalar() or 0
            docs = (await session.execute(stmt)).scalars().all()

        return DocumentListResponse(
            documents=[self._doc_to_metadata(d) for d in docs],
            total_count=total_count,
            limit=limit,
            offset=offset,
            trace_id=tid,
        )

    async def get_document_chunks(
        self,
        document_id: str,
        limit: int = 100,
        offset: int = 0,
        trace_id: Optional[str] = None,
    ) -> List[ChunkRetrievalResult]:
        """Retrieve ordered chunk records for a specific document."""
        stmt = (
            select(DocumentChunk, Document)
            .join(Document, DocumentChunk.document_id == Document.id)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index.asc())
            .limit(limit)
            .offset(offset)
        )

        async with self.get_session() as session:
            rows = (await session.execute(stmt)).all()

        return [
            self._chunk_to_result(chunk=c, doc=d) for c, d in rows
        ]
