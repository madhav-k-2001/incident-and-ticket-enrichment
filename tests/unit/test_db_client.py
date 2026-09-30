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
    ChatMessage,
    ChatSession,
    Document,
    DocumentChunk,
    IncidentDraft,
    TicketAudit,
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
        draft = IncidentDraft(
            alarm_id="ALM-100",
            title="High Compressor Temperature",
            severity="critical",
        )
        session.add(draft)

    # Verify committed in separate session
    async with test_db_client.session() as session:
        stmt = select(IncidentDraft).where(IncidentDraft.alarm_id == "ALM-100")
        result = await session.execute(stmt)
        saved = result.scalar_one()
        assert saved.title == "High Compressor Temperature"
        assert saved.severity == "critical"
        assert saved.status == "draft"
        assert saved.id is not None
        assert saved.created_at is not None


@pytest.mark.asyncio
async def test_session_rollback_on_error(test_db_client: DatabaseClient):
    with pytest.raises(RuntimeError, match="Simulated failure"):
        async with test_db_client.session() as session:
            draft = IncidentDraft(
                alarm_id="ALM-FAIL",
                title="Should Be Rolled Back",
            )
            session.add(draft)
            raise RuntimeError("Simulated failure")

    # Verify not committed
    async with test_db_client.session() as session:
        stmt = select(IncidentDraft).where(IncidentDraft.alarm_id == "ALM-FAIL")
        result = await session.execute(stmt)
        assert result.scalar_one_or_none() is None


class SampleChatService(BaseDatabaseService):
    """Example domain service demonstrating BaseDatabaseService usage."""

    async def create_chat_session(self, title: str) -> ChatSession:
        async with self.get_session() as session:
            chat = ChatSession(title=title)
            session.add(chat)
            await session.flush()
            return chat

    async def post_message(
        self, session_id: str, role: str, content: str
    ) -> ChatMessage:
        async with self.get_session() as session:
            msg = ChatMessage(session_id=session_id, role=role, content=content)
            session.add(msg)
            await session.flush()
            return msg

    async def get_session_history(self, session_id: str) -> ChatSession | None:
        async with self.get_session() as session:
            stmt = (
                select(ChatSession)
                .where(ChatSession.id == session_id)
                .options(selectinload(ChatSession.messages))
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()


@pytest.mark.asyncio
async def test_service_class_integration(test_db_client: DatabaseClient):
    # Test service using DatabaseClient
    service = SampleChatService(db=test_db_client)
    chat = await service.create_chat_session("Compressor Overheat Investigation")
    assert chat.id is not None

    msg1 = await service.post_message(
        session_id=chat.id,
        role="user",
        content="Show active alarms in EastRefinery",
    )
    msg2 = await service.post_message(
        session_id=chat.id,
        role="assistant",
        content="Found 2 critical alarms: ALM-9021 and ALM-9022.",
    )

    history = await service.get_session_history(chat.id)
    assert history is not None
    assert len(history.messages) == 2
    assert history.messages[0].role == "user"
    assert history.messages[1].role == "assistant"
    assert "EastRefinery" in history.messages[0].content


@pytest.mark.asyncio
async def test_service_with_injected_session(test_db_client: DatabaseClient):
    # Test service using an injected request-scoped AsyncSession
    async with test_db_client.session() as session:
        service = SampleChatService(db=session)
        audit = TicketAudit(
            ticket_id="INC-8821",
            alarm_id="ALM-9021",
            action="create_ticket",
            status="success",
        )
        session.add(audit)

    async with test_db_client.session() as session:
        stmt = select(TicketAudit).where(TicketAudit.ticket_id == "INC-8821")
        result = await session.execute(stmt)
        record = result.scalar_one()
        assert record.action == "create_ticket"
        assert record.status == "success"


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

