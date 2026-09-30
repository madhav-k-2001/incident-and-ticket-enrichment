"""Unit tests for User, ChatSession, and ChatMessage SQLAlchemy models."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from db.client import DatabaseClient
from db.config import DatabaseConfig
from db.models import (
    ChatMessage,
    ChatSession,
    MessageType,
    SenderType,
    SessionStatus,
    User,
)


@pytest.fixture
async def test_db():
    """In-memory async SQLite client fixture for isolated unit testing."""
    config = DatabaseConfig(database_url="sqlite+aiosqlite:///:memory:")
    client = DatabaseClient(config=config)
    await client.create_tables()
    yield client
    await client.drop_tables()
    await client.disconnect()


@pytest.mark.asyncio
async def test_create_user(test_db: DatabaseClient):
    async with test_db.session() as session:
        user = User(
            username="ops_engineer",
            email="ops@company.internal",
            full_name="Operations Engineer",
            role="operator",
        )
        session.add(user)

    async with test_db.session() as session:
        stmt = select(User).where(User.username == "ops_engineer")
        result = await session.execute(stmt)
        saved = result.scalar_one()

        assert saved.username == "ops_engineer"
        assert saved.email == "ops@company.internal"
        assert saved.role == "operator"
        assert saved.is_active is True
        assert isinstance(saved.id, uuid.UUID)
        assert saved.created_at is not None
        assert saved.updated_at is not None

        user_dict = saved.to_dict()
        assert user_dict["username"] == "ops_engineer"
        assert user_dict["id"] == str(saved.id)


@pytest.mark.asyncio
async def test_create_chat_session_with_user(test_db: DatabaseClient):
    async with test_db.session() as session:
        user = User(username="incident_investigator", role="engineer")
        session.add(user)
        await session.flush()

        chat_session = ChatSession(
            user_id=user.id,
            session_status=SessionStatus.ACTIVE.value,
            session_summary="Investigating high pressure alarm on compressor",
        )
        session.add(chat_session)

    async with test_db.session() as session:
        stmt = (
            select(ChatSession)
            .options(selectinload(ChatSession.user))
            .where(ChatSession.session_status == "active")
        )
        result = await session.execute(stmt)
        saved = result.scalar_one()

        assert isinstance(saved.id, uuid.UUID)
        assert saved.user_id == user.id
        assert saved.user.username == "incident_investigator"
        assert saved.total_messages == 0
        assert saved.session_summary == "Investigating high pressure alarm on compressor"
        assert not hasattr(saved, "appointment_requested")
        assert saved.started_at is not None
        assert saved.created_at is not None

        session_dict = saved.to_dict()
        assert session_dict["id"] == str(saved.id)
        assert session_dict["user_id"] == str(user.id)
        assert session_dict["session_status"] == "active"


@pytest.mark.asyncio
async def test_chat_messages_ordering_and_relationship(test_db: DatabaseClient):
    async with test_db.session() as session:
        user = User(username="analyst_1")
        session.add(user)
        await session.flush()

        chat_session = ChatSession(user_id=user.id)
        session.add(chat_session)
        await session.flush()

        msg1 = ChatMessage(
            session_id=chat_session.id,
            message_sequence=1,
            sender_type="user",
            message_content="Show active alarms for Pump 402",
            message_type="text",
        )
        msg2 = ChatMessage(
            session_id=chat_session.id,
            message_sequence=2,
            sender_type="bot",
            message_content="Found 2 critical alarms for Pump 402",
            message_type="text",
            metadata={"alarms_count": 2, "asset": "PUMP-402"},
        )
        session.add_all([msg1, msg2])
        chat_session.total_messages = 2

    async with test_db.session() as session:
        stmt = (
            select(ChatSession)
            .options(selectinload(ChatSession.messages))
            .where(ChatSession.user_id == user.id)
        )
        result = await session.execute(stmt)
        saved = result.scalar_one()

        assert len(saved.messages) == 2
        assert saved.messages[0].message_sequence == 1
        assert saved.messages[0].sender_type == "user"
        assert saved.messages[0].message_content == "Show active alarms for Pump 402"
        assert saved.messages[1].message_sequence == 2
        assert saved.messages[1].sender_type == "bot"
        assert saved.messages[1].metadata_ == {"alarms_count": 2, "asset": "PUMP-402"}
        assert saved.messages[1].message_metadata == {"alarms_count": 2, "asset": "PUMP-402"}

        msg_dict = saved.messages[1].to_dict()
        assert msg_dict["metadata"] == {"alarms_count": 2, "asset": "PUMP-402"}
        assert msg_dict["message_sequence"] == 2


@pytest.mark.asyncio
async def test_backward_compatibility_patient_id(test_db: DatabaseClient):
    async with test_db.session() as session:
        user = User(username="legacy_client")
        session.add(user)
        await session.flush()

        # Instantiate using patient_id alias
        chat_session = ChatSession(patient_id=user.id)
        session.add(chat_session)

    async with test_db.session() as session:
        stmt = select(ChatSession).where(ChatSession.user_id == user.id)
        result = await session.execute(stmt)
        saved = result.scalar_one()

        assert saved.patient_id == user.id
        assert saved.user_id == user.id


@pytest.mark.asyncio
async def test_unique_constraint_session_sequence(test_db: DatabaseClient):
    async with test_db.session() as session:
        chat_session = ChatSession(session_status="active")
        session.add(chat_session)
        await session.flush()

        msg1 = ChatMessage(
            session_id=chat_session.id,
            message_sequence=1,
            sender_type="user",
            message_content="First message",
        )
        session.add(msg1)
        await session.flush()

        # Duplicate sequence number for same session should raise IntegrityError
        msg_duplicate = ChatMessage(
            session_id=chat_session.id,
            message_sequence=1,
            sender_type="bot",
            message_content="Duplicate sequence message",
        )
        session.add(msg_duplicate)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_cascade_delete_session_and_messages(test_db: DatabaseClient):
    async with test_db.session() as session:
        user = User(username="to_delete")
        session.add(user)
        await session.flush()

        chat_session = ChatSession(user_id=user.id)
        session.add(chat_session)
        await session.flush()

        msg = ChatMessage(
            session_id=chat_session.id,
            message_sequence=1,
            sender_type="user",
            message_content="Will be deleted",
        )
        session.add(msg)

    async with test_db.session() as session:
        # Delete user should cascade delete chat_sessions and chat_messages
        stmt = select(User).where(User.username == "to_delete")
        user_to_del = (await session.execute(stmt)).scalar_one()
        await session.delete(user_to_del)

    async with test_db.session() as session:
        sessions = (await session.execute(select(ChatSession))).scalars().all()
        assert len(sessions) == 0
        messages = (await session.execute(select(ChatMessage))).scalars().all()
        assert len(messages) == 0
