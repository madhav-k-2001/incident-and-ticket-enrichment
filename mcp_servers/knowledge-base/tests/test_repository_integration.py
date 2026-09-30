"""SQL tests against a real PostgreSQL + pgvector instance.

Opt-in: set KB_INTEGRATION=1 and point POSTGRES_* at a server with the `vector`
extension available (e.g. the ingestion stack's `db` service on port 5434).
Uses a separate `kb_mcp_test` database, created from scratch on every run with
the same DDL the ingestion service generates.
"""

import os

import asyncpg
import pytest

from kb_mcp.config import Settings
from kb_mcp.embedder import mock_embedding
from kb_mcp.errors import InvalidRequestError, UnavailableError
from kb_mcp.repository import PgVectorRepository, _vector_literal

pytestmark = pytest.mark.skipif(os.environ.get("KB_INTEGRATION") != "1", reason="set KB_INTEGRATION=1 to run")

TEST_DB = "kb_mcp_test"

# Mirrors ingestion/app/db/models.py (SQLAlchemy create_all output).
SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE documents (
    id VARCHAR(36) PRIMARY KEY, filename VARCHAR(255) NOT NULL, file_type VARCHAR(10) NOT NULL,
    file_size INTEGER NOT NULL, file_path VARCHAR(500) NOT NULL, status VARCHAR(30) NOT NULL,
    total_chunks INTEGER NOT NULL, processed_chunks INTEGER NOT NULL, error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE document_chunks (
    id VARCHAR(36) PRIMARY KEY, document_id VARCHAR(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL, page_number INTEGER, content TEXT NOT NULL, char_count INTEGER NOT NULL,
    estimated_tokens INTEGER NOT NULL, embedding vector(768) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_doc_chunk_embedding_hnsw ON document_chunks USING hnsw (embedding vector_cosine_ops);
"""

DOCS = {
    "d-sop": ("SOP-CMP-201_Overpressure.pdf", "COMPLETED", [
        "Confirm the discharge pressure alarm on compressor CMP-201.",
        "Check the anti-surge valve position and recycle flow.",
    ]),
    "d-kb": ("KB-BFP-101-Plate-Cooler.pdf", "COMPLETED", ["Descale the lube oil plate cooler on BFP-101."]),
    "d-new": ("SAF-02.pdf", "PROCESSING", []),
}


@pytest.fixture
def settings() -> Settings:
    return Settings(postgres_db=TEST_DB)


@pytest.fixture
async def repo(settings: Settings):
    admin = await asyncpg.connect(Settings().dsn)
    await admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    await admin.execute(f"CREATE DATABASE {TEST_DB}")
    await admin.close()

    conn = await asyncpg.connect(settings.dsn)
    await conn.execute(SCHEMA)
    for doc_id, (filename, status, chunks) in DOCS.items():
        await conn.execute(
            "INSERT INTO documents VALUES ($1, $2, 'pdf', 10, '/srv/uploads/x', $3, $4, $4, NULL)",
            doc_id, filename, status, len(chunks),
        )
        for i, text in enumerate(chunks):
            await conn.execute(
                "INSERT INTO document_chunks VALUES ($1, $2, $3, 1, $4, $5, 1, $6::vector)",
                f"{doc_id}-{i}", doc_id, i, text, len(text), _vector_literal(mock_embedding(text, 768)),
            )
    await conn.close()

    repository = PgVectorRepository(settings)
    yield repository
    await repository.close()


async def test_list_and_get_documents(repo: PgVectorRepository) -> None:
    rows, total = await repo.list_documents(status="COMPLETED", filename_contains=None, limit=1, offset=0)
    assert total == 2 and len(rows) == 1
    assert "file_path" not in rows[0]

    _, total = await repo.list_documents(status=None, filename_contains=None, limit=10, offset=50)
    assert total == 3

    # `_` is escaped, so it only matches a literal underscore.
    rows, _ = await repo.list_documents(status=None, filename_contains="201_", limit=10, offset=0)
    assert [r["id"] for r in rows] == ["d-sop"]

    assert (await repo.get_document("d-kb"))["filename"] == "KB-BFP-101-Plate-Cooler.pdf"
    assert await repo.get_document("nope") is None


async def test_get_chunks_range(repo: PgVectorRepository) -> None:
    rows = await repo.get_chunks("d-sop", first=1, last=5)
    assert [r["chunk_index"] for r in rows] == [1]


async def test_semantic_search_finds_the_exact_chunk_first(repo: PgVectorRepository) -> None:
    text = "Check the anti-surge valve position and recycle flow."
    rows = await repo.semantic_search(mock_embedding(text, 768), limit=3, document_ids=None, filename_contains=None)

    assert rows[0]["chunk_id"] == "d-sop-1"
    assert rows[0]["similarity"] == pytest.approx(1.0, abs=1e-5)

    filtered = await repo.semantic_search(mock_embedding(text, 768), limit=3, document_ids=["d-kb"], filename_contains=None)
    assert [r["document_id"] for r in filtered] == ["d-kb"]


async def test_keyword_search_matches_any_term_and_ranks(repo: PgVectorRepository) -> None:
    rows = await repo.keyword_search("stuck anti-surge valve", limit=5, document_ids=None, filename_contains=None)

    assert [r["chunk_id"] for r in rows] == ["d-sop-1"]
    assert rows[0]["keyword_rank"] > 0

    rows = await repo.keyword_search("BFP-101 cooler", limit=5, document_ids=None, filename_contains="KB-")
    assert [r["chunk_id"] for r in rows] == ["d-kb-0"]


async def test_stats(repo: PgVectorRepository) -> None:
    stats = await repo.stats()

    assert stats == {
        "documents_by_status": {"COMPLETED": 2, "PROCESSING": 1},
        "total_chunks": 3,
        "stored_embedding_dimension": 768,
    }


async def test_dimension_mismatch_is_an_invalid_request(repo: PgVectorRepository) -> None:
    with pytest.raises(InvalidRequestError):
        await repo.semantic_search([0.1] * 3, limit=1, document_ids=None, filename_contains=None)


async def test_unreachable_database_is_unavailable() -> None:
    repo = PgVectorRepository(Settings(postgres_port=1, db_command_timeout_seconds=2))
    with pytest.raises(UnavailableError):
        await repo.get_document("x")
