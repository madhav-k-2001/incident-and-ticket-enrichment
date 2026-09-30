"""Unit tests for SQLAlchemy DatabaseClient, DatabaseConfig, and Models."""

from __future__ import annotations

import os
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.base_service import BaseDatabaseService
from db.client import DatabaseClient
from db.config import DatabaseConfig
from db.models import (
    Base,
    Document,
    DocumentChunk,
)
from db.session import get_db, set_default_client


class TestDatabaseConfig:
    def test_default_config(self):
        config = DatabaseConfig(
            host="db.internal",
            port=5432,
            user="app_user",
            password="secret_password",
            database="telemetry_db",
        )
        assert config.async_url == "postgresql+asyncpg://app_user:secret_password@db.internal:5432/telemetry_db"
        assert config.sync_url == "postgresql://app_user:secret_password@db.internal:5432/telemetry_db"
        assert "secret_password" not in config.masked_url()
        assert "***" in config.masked_url()
        assert "secret_password" not in repr(config)

    def test_url_normalization(self):
        # Normalize postgresql:// to postgresql+asyncpg://
        cfg1 = DatabaseConfig(database_url="postgresql://user:pass@localhost:5432/db")
        assert cfg1.async_url == "postgresql+asyncpg://user:pass@localhost:5432/db"
        assert cfg1.sync_url == "postgresql://user:pass@localhost:5432/db"

        # Normalize postgres:// to postgresql+asyncpg://
        cfg2 = DatabaseConfig(database_url="postgres://user:pass@localhost:5432/db")
        assert cfg2.async_url == "postgresql+asyncpg://user:pass@localhost:5432/db"

        # Normalize sqlite:// to sqlite+aiosqlite://
        cfg3 = DatabaseConfig(database_url="sqlite:///test.db")
        assert cfg3.async_url == "sqlite+aiosqlite:///test.db"
        assert cfg3.sync_url == "sqlite:///test.db"


@pytest.fixture
async def test_db_client():
    """In-memory async SQLite client fixture for isolated unit testing."""
    config = DatabaseConfig(database_url="sqlite+aiosqlite:///:memory:")
    client = DatabaseClient(config=config)
    await client.create_tables()
    yield client
    await client.drop_tables()
    await client.disconnect()


@pytest.mark.asyncio
async def test_ping_and_health_check(test_db_client: DatabaseClient):
    assert await test_db_client.ping() is True
    health = await test_db_client.check_health()
    assert health["status"] == "healthy"
    assert health["connected"] is True
    assert isinstance(health["latency_ms"], float)


@pytest.mark.asyncio
async def test_session_commit_on_success(test_db_client: DatabaseClient):
    async with test_db_client.session() as session:
        doc = Document(
            filename="Standard_Alarm_Procedure.pdf",
            file_type="pdf",
            file_size=10240,
            file_path="/docs/sop.pdf",
            status="PENDING",
        )
        session.add(doc)

    # Verify committed in separate session
    async with test_db_client.session() as session:
        stmt = select(Document).where(Document.filename == "Standard_Alarm_Procedure.pdf")
        result = await session.execute(stmt)
        saved = result.scalar_one()
        assert saved.filename == "Standard_Alarm_Procedure.pdf"
        assert saved.file_type == "pdf"
        assert saved.status == "PENDING"
        assert saved.id is not None
        assert saved.created_at is not None


@pytest.mark.asyncio
async def test_session_rollback_on_error(test_db_client: DatabaseClient):
    with pytest.raises(RuntimeError, match="Simulated failure"):
        async with test_db_client.session() as session:
            doc = Document(
                filename="Should_Be_Rolled_Back.pdf",
                file_type="pdf",
                file_size=5000,
                file_path="/docs/rollback.pdf",
            )
            session.add(doc)
            raise RuntimeError("Simulated failure")

    # Verify not committed
    async with test_db_client.session() as session:
        stmt = select(Document).where(Document.filename == "Should_Be_Rolled_Back.pdf")
        result = await session.execute(stmt)
        assert result.scalar_one_or_none() is None


class SampleDocumentService(BaseDatabaseService):
    """Example domain service demonstrating BaseDatabaseService usage with Document models."""

    async def register_document(
        self, filename: str, file_type: str, file_size: int, file_path: str
    ) -> Document:
        async with self.get_session() as session:
            doc = Document(
                filename=filename,
                file_type=file_type,
                file_size=file_size,
                file_path=file_path,
                status="PENDING",
            )
            session.add(doc)
            await session.flush()
            return doc

    async def add_chunk(
        self, document_id: str, chunk_index: int, content: str, embedding: list[float]
    ) -> DocumentChunk:
        async with self.get_session() as session:
            chunk = DocumentChunk(
                document_id=document_id,
                chunk_index=chunk_index,
                content=content,
                char_count=len(content),
                estimated_tokens=len(content) // 4,
                embedding=embedding,
            )
            session.add(chunk)
            await session.flush()
            return chunk

    async def get_document_with_chunks(self, document_id: str) -> Document | None:
        async with self.get_session() as session:
            stmt = (
                select(Document)
                .where(Document.id == document_id)
                .options(selectinload(Document.chunks))
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()


@pytest.mark.asyncio
async def test_service_class_integration(test_db_client: DatabaseClient):
    # Test service using DatabaseClient
    service = SampleDocumentService(db=test_db_client)
    doc = await service.register_document(
        filename="Compressor_Troubleshooting.pdf",
        file_type="pdf",
        file_size=150000,
        file_path="/uploads/Compressor_Troubleshooting.pdf",
    )
    assert doc.id is not None

    chunk1 = await service.add_chunk(
        document_id=doc.id,
        chunk_index=0,
        content="Overview of wet gas compressor discharge pressure alarms.",
        embedding=[0.1] * 768,
    )
    chunk2 = await service.add_chunk(
        document_id=doc.id,
        chunk_index=1,
        content="Recommended actions: inspect bypass valve, check discharge filter.",
        embedding=[0.2] * 768,
    )

    retrieved = await service.get_document_with_chunks(doc.id)
    assert retrieved is not None
    assert len(retrieved.chunks) == 2
    assert retrieved.chunks[0].chunk_index == 0
    assert retrieved.chunks[1].chunk_index == 1
    assert "discharge pressure" in retrieved.chunks[0].content


@pytest.mark.asyncio
async def test_service_with_injected_session(test_db_client: DatabaseClient):
    # Test service using an injected request-scoped AsyncSession
    async with test_db_client.session() as session:
        service = SampleDocumentService(db=session)
        doc = await service.register_document(
            filename="Injected_Session_Doc.pdf",
            file_type="pdf",
            file_size=4096,
            file_path="/docs/injected.pdf",
        )
        assert doc.id is not None

    async with test_db_client.session() as session:
        stmt = select(Document).where(Document.filename == "Injected_Session_Doc.pdf")
        result = await session.execute(stmt)
        record = result.scalar_one()
        assert record.file_size == 4096
        assert record.status == "PENDING"


@pytest.mark.asyncio
async def test_get_db_fastapi_dependency(test_db_client: DatabaseClient):
    set_default_client(test_db_client)
    async for session in get_db():
        assert isinstance(session, AsyncSession)
        stmt = select(1)
        result = await session.execute(stmt)
        assert result.scalar() == 1
    set_default_client(None)


@pytest.mark.asyncio
async def test_document_and_chunk_lifecycle(test_db_client: DatabaseClient):
    # 1. Create a document with chunk records
    async with test_db_client.session() as session:
        doc = Document(
            filename="Compressor_SOP_Rev3.pdf",
            file_type="pdf",
            file_size=204800,
            file_path="/uploads/Compressor_SOP_Rev3.pdf",
            total_chunks=2,
            processed_chunks=1,
            status="PROCESSING",
        )
        session.add(doc)
        await session.flush()
        doc_id = doc.id

        chunk1 = DocumentChunk(
            document_id=doc_id,
            chunk_index=0,
            page_number=1,
            content="Standard operating procedure for Wet Gas Compressor discharge pressure high alarm.",
            char_count=82,
            estimated_tokens=18,
            embedding=[0.05] * 768,
        )
        chunk2 = DocumentChunk(
            document_id=doc_id,
            chunk_index=1,
            page_number=2,
            content="Check anti-surge valve position and inspect downstream coolers.",
            char_count=64,
            estimated_tokens=14,
            embedding=[0.02] * 768,
        )
        session.add_all([chunk1, chunk2])

    # 2. Verify document and chunks were saved and relationship loads
    async with test_db_client.session() as session:
        stmt = (
            select(Document)
            .where(Document.id == doc_id)
            .options(selectinload(Document.chunks))
        )
        result = await session.execute(stmt)
        retrieved_doc = result.scalar_one()

        assert retrieved_doc.filename == "Compressor_SOP_Rev3.pdf"
        assert len(retrieved_doc.chunks) == 2
        assert retrieved_doc.chunks[0].chunk_index == 0
        assert retrieved_doc.chunks[1].chunk_index == 1

        # Test to_dict methods
        doc_dict = retrieved_doc.to_dict()
        assert doc_dict["progress_percent"] == 50.0
        assert doc_dict["status"] == "PROCESSING"

        chunk_dict_no_embed = retrieved_doc.chunks[0].to_dict(include_embedding=False)
        assert "embedding" not in chunk_dict_no_embed
        assert chunk_dict_no_embed["estimated_tokens"] == 18

        chunk_dict_with_embed = retrieved_doc.chunks[0].to_dict(include_embedding=True)
        assert "embedding" in chunk_dict_with_embed
        assert len(chunk_dict_with_embed["embedding"]) == 768

    # 3. Test cascade delete: deleting document deletes chunks
    async with test_db_client.session() as session:
        stmt = select(Document).where(Document.id == doc_id)
        result = await session.execute(stmt)
        doc_to_delete = result.scalar_one()
        await session.delete(doc_to_delete)

    async with test_db_client.session() as session:
        chunks_stmt = select(DocumentChunk).where(DocumentChunk.document_id == doc_id)
        chunks_res = await session.execute(chunks_stmt)
        assert len(chunks_res.scalars().all()) == 0
