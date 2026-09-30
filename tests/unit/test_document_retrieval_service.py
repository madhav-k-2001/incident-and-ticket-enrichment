"""Unit tests for DocumentRetrievalService and pgvector retrieval operations."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest

from db.models.document import Document, DocumentChunk
from mcp_servers.exceptions import NotFoundError, ValidationError
from mcp_servers.services.document_retrieval_service import DocumentRetrievalService


def _create_mock_doc(
    doc_id: str = "doc-1",
    filename: str = "compressor_sop.pdf",
    file_type: str = "pdf",
    status: str = "COMPLETED",
    total_chunks: int = 5,
    processed_chunks: int = 5,
) -> Document:
    doc = Document(
        id=doc_id,
        filename=filename,
        file_type=file_type,
        file_size=10240,
        file_path=f"/docs/{filename}",
        status=status,
        total_chunks=total_chunks,
        processed_chunks=processed_chunks,
        created_at=datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 1, 12, 5, 0, tzinfo=timezone.utc),
    )
    return doc


def _create_mock_chunk(
    chunk_id: str = "chk-1",
    doc_id: str = "doc-1",
    chunk_index: int = 0,
    page_number: int = 1,
    content: str = "Emergency shutdown procedure for compressor unit K-101.",
) -> DocumentChunk:
    chunk = DocumentChunk(
        id=chunk_id,
        document_id=doc_id,
        chunk_index=chunk_index,
        page_number=page_number,
        content=content,
        char_count=len(content),
        estimated_tokens=len(content.split()),
        embedding=[0.01] * 768,
        created_at=datetime(2026, 5, 1, 12, 1, 0, tzinfo=timezone.utc),
    )
    return chunk


class TestDocumentRetrievalServiceInit:
    def test_default_embedding_dimension(self):
        service = DocumentRetrievalService()
        assert service.embedding_dimension == 768

    def test_custom_embedding_dimension(self):
        service = DocumentRetrievalService(embedding_dimension=1536)
        assert service.embedding_dimension == 1536

    def test_vector_validation_success(self):
        service = DocumentRetrievalService(embedding_dimension=4)
        vec = service._validate_vector([0.1, 0.2, 0.3, 0.4])
        assert vec == [0.1, 0.2, 0.3, 0.4]

    def test_vector_validation_wrong_dim(self):
        service = DocumentRetrievalService(embedding_dimension=4)
        with pytest.raises(ValidationError, match="dimension mismatch"):
            service._validate_vector([0.1, 0.2])

    def test_vector_validation_invalid_type(self):
        service = DocumentRetrievalService(embedding_dimension=4)
        with pytest.raises(ValidationError, match="must be a sequence"):
            service._validate_vector("not-a-vector")  # type: ignore


@pytest.mark.asyncio
class TestSimilaritySearch:
    async def test_cosine_similarity_search(self):
        doc = _create_mock_doc()
        chunk = _create_mock_chunk()

        # Mock session returning a single chunk with cosine distance 0.2
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [(chunk, doc, 0.2)]
        mock_session.execute.return_value = mock_result

        service = DocumentRetrievalService(db=mock_session, embedding_dimension=768)
        query_vec = [0.05] * 768

        response = await service.similarity_search(
            query_embedding=query_vec,
            top_k=5,
            trace_id="test-trace-1",
        )

        assert response.search_type == "similarity"
        assert response.total_results == 1
        assert response.trace_id == "test-trace-1"
        assert len(response.results) == 1

        result = response.results[0]
        assert result.chunk_id == "chk-1"
        assert result.document_id == "doc-1"
        assert result.filename == "compressor_sop.pdf"
        assert result.distance == 0.2
        assert result.similarity_score == 0.8  # 1.0 - 0.2
        assert result.chunk_index == 0

    async def test_l2_distance_metric(self):
        doc = _create_mock_doc()
        chunk = _create_mock_chunk()

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [(chunk, doc, 1.0)]
        mock_session.execute.return_value = mock_result

        service = DocumentRetrievalService(db=mock_session, embedding_dimension=768)
        query_vec = [0.05] * 768

        response = await service.similarity_search(
            query_embedding=query_vec,
            distance_metric="l2",
        )
        assert len(response.results) == 1
        # L2 score is 1.0 / (1.0 + 1.0) = 0.5
        assert response.results[0].similarity_score == 0.5

    async def test_max_inner_product_metric(self):
        doc = _create_mock_doc()
        chunk = _create_mock_chunk()

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [(chunk, doc, -0.75)]
        mock_session.execute.return_value = mock_result

        service = DocumentRetrievalService(db=mock_session, embedding_dimension=768)
        query_vec = [0.05] * 768

        response = await service.similarity_search(
            query_embedding=query_vec,
            distance_metric="max_inner_product",
        )
        assert len(response.results) == 1
        assert response.results[0].similarity_score == 0.75

    async def test_similarity_threshold_filters_out_low_scores(self):
        doc = _create_mock_doc()
        chunk1 = _create_mock_chunk(chunk_id="c1", chunk_index=0)
        chunk2 = _create_mock_chunk(chunk_id="c2", chunk_index=1)

        # chunk1: dist 0.1 => sim 0.9; chunk2: dist 0.5 => sim 0.5
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [(chunk1, doc, 0.1), (chunk2, doc, 0.5)]
        mock_session.execute.return_value = mock_result

        service = DocumentRetrievalService(db=mock_session, embedding_dimension=768)
        query_vec = [0.05] * 768

        response = await service.similarity_search(
            query_embedding=query_vec,
            similarity_threshold=0.8,
        )
        assert len(response.results) == 1
        assert response.results[0].chunk_id == "c1"

    async def test_unsupported_distance_metric_raises(self):
        service = DocumentRetrievalService(embedding_dimension=4)
        with pytest.raises(ValidationError, match="Unsupported distance metric"):
            await service.similarity_search(
                query_embedding=[0.1, 0.2, 0.3, 0.4],
                distance_metric="hamming",  # type: ignore
            )


@pytest.mark.asyncio
class TestKeywordSearch:
    async def test_keyword_search_matches(self):
        doc = _create_mock_doc()
        chunk = _create_mock_chunk(content="High vibration detected on compressor bearing.")

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [(chunk, doc)]
        mock_session.execute.return_value = mock_result

        service = DocumentRetrievalService(db=mock_session)
        response = await service.keyword_search("vibration")

        assert response.search_type == "keyword"
        assert response.query == "vibration"
        assert len(response.results) == 1
        assert response.results[0].content == "High vibration detected on compressor bearing."
        assert response.results[0].similarity_score == 1.0

    async def test_empty_keyword_returns_empty(self):
        service = DocumentRetrievalService()
        response = await service.keyword_search("   ")
        assert response.results == []
        assert response.total_results == 0


@pytest.mark.asyncio
class TestHybridSearch:
    async def test_hybrid_search_rrf_merging(self):
        doc = _create_mock_doc()
        chunk1 = _create_mock_chunk(chunk_id="c1", content="Motor trip alarm procedure.")
        chunk2 = _create_mock_chunk(chunk_id="c2", content="Bearing temperature limit.")

        service = DocumentRetrievalService(embedding_dimension=768)

        # Mock similarity_search and keyword_search
        vec_res = service._chunk_to_result(chunk=chunk1, doc=doc, similarity_score=0.9, distance=0.1)
        key_res = service._chunk_to_result(chunk=chunk2, doc=doc, similarity_score=1.0)

        async def mock_sim_search(**kwargs):
            from mcp_servers.schemas.documents import DocumentRetrievalResponse
            return DocumentRetrievalResponse(results=[vec_res], total_results=1, search_type="similarity")

        async def mock_key_search(**kwargs):
            from mcp_servers.schemas.documents import DocumentRetrievalResponse
            return DocumentRetrievalResponse(results=[key_res, vec_res], total_results=2, search_type="keyword")

        service.similarity_search = AsyncMock(side_effect=mock_sim_search)
        service.keyword_search = AsyncMock(side_effect=mock_key_search)

        resp = await service.hybrid_search(
            query="alarm procedure",
            query_embedding=[0.05] * 768,
            top_k=2,
        )

        assert resp.search_type == "hybrid"
        assert len(resp.results) == 2
        # c1 appeared in both vector and keyword, so it should rank first with score 1.0
        assert resp.results[0].chunk_id == "c1"
        assert resp.results[0].similarity_score == 1.0
        assert resp.results[1].chunk_id == "c2"


@pytest.mark.asyncio
class TestChunkContext:
    async def test_get_chunk_context_window(self):
        doc = _create_mock_doc()
        chunk_prev = _create_mock_chunk(chunk_id="c0", chunk_index=0, content="Section 1: Initial checks.")
        chunk_target = _create_mock_chunk(chunk_id="c1", chunk_index=1, content="Section 2: Alarm isolation.")
        chunk_next = _create_mock_chunk(chunk_id="c2", chunk_index=2, content="Section 3: Reset procedure.")

        mock_session = AsyncMock()

        # Step 1: Target chunk query
        mock_target_res = MagicMock()
        mock_target_res.first.return_value = (chunk_target, doc)

        # Step 2: Preceding chunks query
        mock_prec_res = MagicMock()
        mock_prec_res.all.return_value = [(chunk_prev, doc)]

        # Step 3: Following chunks query
        mock_foll_res = MagicMock()
        mock_foll_res.all.return_value = [(chunk_next, doc)]

        mock_session.execute.side_effect = [
            mock_target_res,
            mock_prec_res,
            mock_foll_res,
        ]

        service = DocumentRetrievalService(db=mock_session)
        ctx = await service.get_chunk_context("c1", window_size=1)

        assert ctx.target_chunk.chunk_id == "c1"
        assert len(ctx.preceding_chunks) == 1
        assert ctx.preceding_chunks[0].chunk_id == "c0"
        assert len(ctx.following_chunks) == 1
        assert ctx.following_chunks[0].chunk_id == "c2"
        assert "Section 1: Initial checks." in ctx.expanded_content
        assert "Section 2: Alarm isolation." in ctx.expanded_content
        assert "Section 3: Reset procedure." in ctx.expanded_content

    async def test_chunk_not_found_raises(self):
        mock_session = AsyncMock()
        mock_target_res = MagicMock()
        mock_target_res.first.return_value = None
        mock_session.execute.return_value = mock_target_res

        service = DocumentRetrievalService(db=mock_session)
        with pytest.raises(NotFoundError, match="not found"):
            await service.get_chunk_context("non-existent")


@pytest.mark.asyncio
class TestDocumentManagement:
    async def test_get_document_success(self):
        doc = _create_mock_doc(status="COMPLETED", total_chunks=10, processed_chunks=10)
        mock_session = AsyncMock()
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = doc
        mock_session.execute.return_value = mock_res

        service = DocumentRetrievalService(db=mock_session)
        meta = await service.get_document("doc-1")

        assert meta is not None
        assert meta.id == "doc-1"
        assert meta.filename == "compressor_sop.pdf"
        assert meta.status == "COMPLETED"
        assert meta.progress_percent == 100.0

    async def test_get_document_not_found(self):
        mock_session = AsyncMock()
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_res

        service = DocumentRetrievalService(db=mock_session)
        meta = await service.get_document("missing-doc")
        assert meta is None

    async def test_list_documents(self):
        doc = _create_mock_doc()
        mock_session = AsyncMock()
        mock_count_res = MagicMock()
        mock_count_res.scalar.return_value = 1
        mock_docs_res = MagicMock()
        mock_docs_res.scalars.return_value.all.return_value = [doc]

        mock_session.execute.side_effect = [mock_count_res, mock_docs_res]

        service = DocumentRetrievalService(db=mock_session)
        resp = await service.list_documents(status="COMPLETED", limit=10)

        assert resp.total_count == 1
        assert len(resp.documents) == 1
        assert resp.documents[0].id == "doc-1"

    async def test_get_document_chunks(self):
        doc = _create_mock_doc()
        c0 = _create_mock_chunk(chunk_id="c0", chunk_index=0)
        c1 = _create_mock_chunk(chunk_id="c1", chunk_index=1)

        mock_session = AsyncMock()
        mock_res = MagicMock()
        mock_res.all.return_value = [(c0, doc), (c1, doc)]
        mock_session.execute.return_value = mock_res

        service = DocumentRetrievalService(db=mock_session)
        chunks = await service.get_document_chunks("doc-1")

        assert len(chunks) == 2
        assert chunks[0].chunk_index == 0
        assert chunks[1].chunk_index == 1
