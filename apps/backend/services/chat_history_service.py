"""Service managing chat sessions and message history using SQLAlchemy."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, List, Optional, Union
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.backend.models.chat_session_model import (
    ChatMessage,
    ChatSession,
    MessageType,
    SenderType,
    SessionStatus,
)
from db.base_service import BaseDatabaseService
from db.client import DatabaseClient
from db.models import ChatMessage as ORMChatMessage, ChatSession as ORMChatSession


class ChatHistoryService(BaseDatabaseService):
    """Domain service managing chat session lifecycle and message history."""

    def __init__(
        self,
        db: Optional[Union[DatabaseClient, AsyncSession]] = None,
        postgres_client: Optional[Any] = None,
    ) -> None:
        """
        Initialize with a DatabaseClient or an existing AsyncSession.
        
        Supports legacy `postgres_client` argument name for backwards compatibility.
        """
        client = db if db is not None else postgres_client
        super().__init__(db=client)

    @asynccontextmanager
    async def _get_db_session(self) -> AsyncIterator[AsyncSession]:
        """Yield database session without colliding with domain get_session()."""
        if self._session is not None:
            yield self._session
        else:
            async with self.client.session() as session:
                yield session

    async def create_session(
        self,
        session_id: UUID,
        patient_id: Optional[UUID] = None,
        *,
        user_id: Optional[UUID] = None,
    ) -> ChatSession:
        """Create and persist a new chat session."""
        target_user_id = user_id or patient_id
        async with self._get_db_session() as session:
            orm_session = ORMChatSession(
                id=session_id,
                user_id=target_user_id,
            )
            session.add(orm_session)
            await session.flush()

            return ChatSession(
                id=orm_session.id,
                user_id=orm_session.user_id,
                session_status=SessionStatus(orm_session.session_status),
                started_at=orm_session.started_at,
                completed_at=orm_session.completed_at,
                total_messages=orm_session.total_messages,
                session_summary=orm_session.session_summary,
                created_at=orm_session.created_at,
            )

    async def get_session(self, session_id: UUID) -> ChatSession | None:
        """Retrieve a chat session by its UUID identifier."""
        async with self._get_db_session() as session:
            stmt = select(ORMChatSession).where(ORMChatSession.id == session_id)
            result = await session.execute(stmt)
            orm_session = result.scalar_one_or_none()
            if not orm_session:
                return None

            return ChatSession(
                id=orm_session.id,
                user_id=orm_session.user_id,
                session_status=SessionStatus(orm_session.session_status),
                started_at=orm_session.started_at,
                completed_at=orm_session.completed_at,
                total_messages=orm_session.total_messages,
                session_summary=orm_session.session_summary,
                created_at=orm_session.created_at,
            )

    async def add_message(self, chat_message: ChatMessage) -> None:
        """Persist a chat message and update the session's total message count."""
        async with self._get_db_session() as session:
            sender_val = (
                chat_message.sender_type.value
                if hasattr(chat_message.sender_type, "value")
                else str(chat_message.sender_type)
            )
            msg_type_val = (
                chat_message.message_type.value
                if hasattr(chat_message.message_type, "value")
                else str(chat_message.message_type)
            )

            orm_message = ORMChatMessage(
                id=chat_message.id,
                session_id=chat_message.session_id,
                message_sequence=chat_message.message_sequence,
                sender_type=sender_val,
                message_content=chat_message.message_content,
                message_type=msg_type_val,
                metadata_=chat_message.metadata,
                timestamp=chat_message.timestamp,
            )
            session.add(orm_message)

            # Keep parent session message count in sync
            stmt = select(ORMChatSession).where(
                ORMChatSession.id == chat_message.session_id
            )
            result = await session.execute(stmt)
            parent_session = result.scalar_one_or_none()
            if parent_session:
                parent_session.total_messages += 1

            await session.flush()

    async def get_session_messages(self, session_id: UUID) -> list[ChatMessage] | None:
        """Retrieve ordered message history for a given session."""
        async with self._get_db_session() as session:
            stmt = (
                select(ORMChatMessage)
                .where(ORMChatMessage.session_id == session_id)
                .order_by(ORMChatMessage.message_sequence.asc())
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            if not rows:
                return None

            return [
                ChatMessage(
                    id=row.id,
                    session_id=row.session_id,
                    message_sequence=row.message_sequence,
                    sender_type=SenderType(row.sender_type),
                    message_content=row.message_content,
                    message_type=MessageType(row.message_type),
                    metadata=row.metadata_,
                    timestamp=row.timestamp,
                )
                for row in rows
            ]
