"""Unit tests for ChatHistoryService using SQLAlchemy DatabaseClient."""

from __future__ import annotations

import uuid
import pytest

from apps.backend.models.chat_session_model import (
    ChatMessage,
    ChatSession,
    MessageType,
    SenderType,
    SessionStatus,
)
from apps.backend.services.chat_history_service import ChatHistoryService
from db.client import DatabaseClient
from db.config import DatabaseConfig
from db.models import User


@pytest.fixture
async def test_db():
    """In-memory async SQLite client fixture for isolated unit testing."""
    config = DatabaseConfig(database_url="sqlite+aiosqlite:///:memory:")
    client = DatabaseClient(config=config)
    await client.create_tables()
    yield client
    await client.drop_tables()
    await client.disconnect()


@pytest.fixture
async def sample_user(test_db: DatabaseClient) -> User:
    """Create a sample user for foreign key references."""
    async with test_db.session() as session:
        user = User(username="operator_bob", email="bob@telemetry.io", role="operator")
        session.add(user)
    return user


@pytest.mark.asyncio
async def test_create_and_get_session(test_db: DatabaseClient, sample_user: User):
    service = ChatHistoryService(db=test_db)
    session_id = uuid.uuid4()

    # 1. Create session with user_id
    created = await service.create_session(session_id=session_id, user_id=sample_user.id)
    assert created.id == session_id
    assert created.user_id == sample_user.id
    assert not hasattr(created, "patient_id")
    assert created.session_status == SessionStatus.active
    assert created.total_messages == 0
    assert created.created_at is not None

    # 2. Retrieve the session
    retrieved = await service.get_session(session_id=session_id)
    assert retrieved is not None
    assert retrieved.id == session_id
    assert retrieved.user_id == sample_user.id
    assert not hasattr(retrieved, "patient_id")
    assert retrieved.session_status == SessionStatus.active
    assert retrieved.total_messages == 0


@pytest.mark.asyncio
async def test_create_session_with_legacy_patient_id(test_db: DatabaseClient, sample_user: User):
    service = ChatHistoryService(postgres_client=test_db)
    session_id = uuid.uuid4()

    # Create session using legacy patient_id argument
    created = await service.create_session(session_id=session_id, patient_id=sample_user.id)
    assert created.id == session_id
    assert created.user_id == sample_user.id

    retrieved = await service.get_session(session_id)
    assert retrieved is not None
    assert retrieved.user_id == sample_user.id


@pytest.mark.asyncio
async def test_get_nonexistent_session(test_db: DatabaseClient):
    service = ChatHistoryService(db=test_db)
    result = await service.get_session(session_id=uuid.uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_add_and_retrieve_messages(test_db: DatabaseClient, sample_user: User):
    service = ChatHistoryService(db=test_db)
    session_id = uuid.uuid4()
    await service.create_session(session_id=session_id, user_id=sample_user.id)

    # Initially empty history
    history = await service.get_session_messages(session_id=session_id)
    assert history is None

    # Add message 1 (user)
    msg1 = ChatMessage(
        session_id=session_id,
        message_sequence=1,
        sender_type=SenderType.user,
        message_content="Correlate alarm ALM-301 with recent compressor trips.",
        message_type=MessageType.text,
    )
    await service.add_message(msg1)

    # Add message 2 (bot) with metadata
    msg2 = ChatMessage(
        session_id=session_id,
        message_sequence=2,
        sender_type=SenderType.bot,
        message_content="Found 3 correlated alarms and 1 runbook SOP-402.",
        message_type=MessageType.text,
        metadata={"correlated_alarms": ["ALM-301", "ALM-302"], "confidence": 0.94},
    )
    await service.add_message(msg2)

    # Verify session message count updated
    updated_session = await service.get_session(session_id=session_id)
    assert updated_session is not None
    assert updated_session.total_messages == 2

    # Verify message sequence ordering and content
    messages = await service.get_session_messages(session_id=session_id)
    assert messages is not None
    assert len(messages) == 2

    assert messages[0].message_sequence == 1
    assert messages[0].sender_type == SenderType.user
    assert "Correlate alarm ALM-301" in messages[0].message_content

    assert messages[1].message_sequence == 2
    assert messages[1].sender_type == SenderType.bot
    assert messages[1].metadata == {"correlated_alarms": ["ALM-301", "ALM-302"], "confidence": 0.94}


@pytest.mark.asyncio
async def test_service_with_injected_session(test_db: DatabaseClient, sample_user: User):
    # Verify service works with an injected request-scoped AsyncSession
    async with test_db.session() as db_session:
        service = ChatHistoryService(db=db_session)
        session_id = uuid.uuid4()
        created = await service.create_session(session_id=session_id, user_id=sample_user.id)
        assert created.id == session_id

        msg = ChatMessage(
            session_id=session_id,
            message_sequence=1,
            sender_type=SenderType.user,
            message_content="Testing with injected transaction session",
        )
        await service.add_message(msg)

        retrieved = await service.get_session(session_id=session_id)
        assert retrieved is not None
        assert retrieved.total_messages == 1
