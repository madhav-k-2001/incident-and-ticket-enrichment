import pytest

from kb_mcp.config import Settings
from kb_mcp.errors import InvalidRequestError, NotFoundError
from kb_mcp.service import KnowledgeBaseService
from tests.conftest import PENDING_ID, SOP_ID, TSG_ID, FakeRepository, FixedEmbedder


@pytest.fixture
def service(repo: FakeRepository, embedder: FixedEmbedder, settings: Settings) -> KnowledgeBaseService:
    return KnowledgeBaseService(repo, embedder, settings)


async def test_semantic_search_ranks_by_similarity(service: KnowledgeBaseService, repo: FakeRepository) -> None:
    result = await service.search("anti-surge valve", mode="semantic", top_k=2)

    assert [h.chunk_id for h in result.hits] == ["ae12ee92-1", "423a7c43-0"]
    assert result.hits[0].score == result.hits[0].similarity == 0.82
    assert [name for name, _ in repo.calls] == ["semantic_search"]


async def test_keyword_search_does_not_embed(service: KnowledgeBaseService, embedder: FixedEmbedder) -> None:
    result = await service.search("CMP-201", mode="keyword")

    assert [h.chunk_id for h in result.hits] == ["423a7c43-0", "ae12ee92-2"]
    assert embedder.queries == []


async def test_hybrid_search_fuses_both_rankings(service: KnowledgeBaseService, repo: FakeRepository) -> None:
    result = await service.search("stuck anti-surge valve", top_k=3)

    # 423a7c43-0 is 2nd semantically and 1st by keyword, so reciprocal rank fusion puts it first.
    assert result.mode == "hybrid"
    assert [h.chunk_id for h in result.hits] == ["423a7c43-0", "ae12ee92-1", "ae12ee92-2"]
    top = result.hits[0]
    assert top.similarity == 0.74 and top.keyword_rank == 0.9
    scores = [h.score for h in result.hits]
    assert scores == sorted(scores, reverse=True)
    # Hybrid over-fetches candidates from each ranker.
    assert {kwargs["limit"] for _, kwargs in repo.calls} == {9}


async def test_min_similarity_filters_vector_matches(service: KnowledgeBaseService) -> None:
    result = await service.search("valve", mode="semantic", min_similarity=0.8)

    assert [h.chunk_id for h in result.hits] == ["ae12ee92-1"]


async def test_embedding_dimension_mismatch_is_reported(repo: FakeRepository) -> None:
    service = KnowledgeBaseService(repo, FixedEmbedder(dimension=3072), Settings(_env_file=None))

    with pytest.raises(InvalidRequestError, match="3072 dimensions"):
        await service.search("valve", mode="semantic")
    assert repo.calls == []


async def test_read_document_paginates(service: KnowledgeBaseService) -> None:
    first = await service.read_document(SOP_ID, max_chunks=2)
    rest = await service.read_document(SOP_ID, start_chunk=first.next_chunk, max_chunks=2)

    assert (first.first_chunk, first.last_chunk, first.next_chunk) == (0, 1, 2)
    assert (rest.first_chunk, rest.last_chunk, rest.next_chunk) == (2, 2, None)
    # Overlap between chunk 0 and 1 is removed when stitching.
    assert first.text.count("pressure alarm on CMP-201") == 1


async def test_read_document_past_the_end_is_invalid(service: KnowledgeBaseService) -> None:
    with pytest.raises(InvalidRequestError):
        await service.read_document(SOP_ID, start_chunk=50)


async def test_chunk_context_returns_neighbours(service: KnowledgeBaseService) -> None:
    passage = await service.get_chunk_context(SOP_ID, 1, window=1)

    assert [c.chunk_index for c in passage.chunks] == [0, 1, 2]
    assert passage.next_chunk is None
    assert "Step 1" in passage.text and "Step 3" in passage.text


async def test_unknown_document_and_chunk_raise_not_found(service: KnowledgeBaseService) -> None:
    with pytest.raises(NotFoundError):
        await service.get_document("missing")
    with pytest.raises(NotFoundError):
        await service.get_chunk_context(TSG_ID, 7)


async def test_document_markdown_has_header_and_deduplicated_body(service: KnowledgeBaseService) -> None:
    markdown = await service.document_markdown(SOP_ID)

    assert markdown.startswith("# SOP-CMP-201-Discharge-Overpressure.pdf\n")
    assert f"`{SOP_ID}`" in markdown
    assert markdown.count("position and recycle flow") == 1
    assert "Step 3: if the valve is stuck" in markdown


async def test_document_markdown_flags_incomplete_ingestion(service: KnowledgeBaseService) -> None:
    markdown = await service.document_markdown(PENDING_ID)

    assert "ingestion is not complete (PROCESSING)" in markdown
    assert "No text has been ingested" in markdown


async def test_searchable_documents_only_lists_completed(service: KnowledgeBaseService) -> None:
    documents = await service.searchable_documents()

    assert {d.id for d in documents} == {SOP_ID, TSG_ID}


async def test_status_reports_counts_and_warnings(service: KnowledgeBaseService, repo: FakeRepository) -> None:
    repo.stored_dimension = 1536
    status = await service.status()

    assert status.total_documents == 3 and status.total_chunks == 5
    assert status.documents_by_status == {"COMPLETED": 2, "PROCESSING": 1}
    assert any("1536 dimensions" in w for w in status.warnings)
    assert any("not fully ingested" in w for w in status.warnings)
