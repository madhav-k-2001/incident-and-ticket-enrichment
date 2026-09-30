"""SQLAlchemy ORM models for chat session management and message history."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from db.models.base import Base

if TYPE_CHECKING:
    from db.models.user import User


def utc_now() -> datetime:
    """Return current UTC timezone-aware datetime."""
    return datetime.now(timezone.utc)


class SessionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class SenderType(str, Enum):
    USER = "user"
    BOT = "bot"
    PATIENT = "patient"


class MessageType(str, Enum):
    TEXT = "text"
    QUICK_REPLY = "quick_reply"
    IMAGE = "image"
    DOCUMENT = "document"
    LOCATION = "location"


class ChatSession(Base):
    """Represents an AI conversation session with a user."""

    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    session_status: Mapped[str] = mapped_column(
        String(20),
        default=SessionStatus.ACTIVE.value,
        server_default=text("'active'"),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    total_messages: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    session_summary: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    user: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="chat_sessions",
    )
    messages: Mapped[List["ChatMessage"]] = relationship(
        "ChatMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.message_sequence",
    )

    __table_args__ = (
        CheckConstraint(
            "session_status IN ('active', 'completed', 'abandoned')",
            name="ck_chat_sessions_session_status",
        ),
    )

    def __init__(self, **kwargs: Any) -> None:
        # Backward-compatible support for patient_id -> user_id
        if "patient_id" in kwargs and "user_id" not in kwargs:
            kwargs["user_id"] = kwargs.pop("patient_id")
        super().__init__(**kwargs)

    @property
    def patient_id(self) -> Optional[uuid.UUID]:
        """Backward-compatible alias for user_id."""
        return self.user_id

    @patient_id.setter
    def patient_id(self, val: Optional[uuid.UUID]) -> None:
        self.user_id = val

    def to_dict(self) -> Dict[str, Any]:
        """Convert session attributes to dictionary."""
        return {
            "id": str(self.id) if self.id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "session_status": self.session_status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "total_messages": self.total_messages,
            "session_summary": self.session_summary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ChatMessage(Base):
    """Represents an individual message within a chat session."""

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    sender_type: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )
    message_content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    message_type: Mapped[str] = mapped_column(
        String(20),
        default=MessageType.TEXT.value,
        server_default=text("'text'"),
        nullable=False,
    )
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata",
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    session: Mapped["ChatSession"] = relationship(
        "ChatSession",
        back_populates="messages",
    )

    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "message_sequence",
            name="uq_chat_messages_session_sequence",
        ),
        CheckConstraint(
            "sender_type IN ('patient', 'bot', 'user')",
            name="ck_chat_messages_sender_type",
        ),
        CheckConstraint(
            "message_type IN ('text', 'quick_reply', 'image', 'document', 'location')",
            name="ck_chat_messages_message_type",
        ),
        Index("ix_chat_messages_session_seq", "session_id", "message_sequence"),
    )

    def __init__(self, **kwargs: Any) -> None:
        # Handle 'metadata' kwarg mapping to avoid conflict with DeclarativeBase.metadata
        if "metadata" in kwargs and "metadata_" not in kwargs:
            kwargs["metadata_"] = kwargs.pop("metadata")
        super().__init__(**kwargs)

    @property
    def message_metadata(self) -> Optional[Dict[str, Any]]:
        """Accessor for JSON metadata."""
        return self.metadata_

    @message_metadata.setter
    def message_metadata(self, val: Optional[Dict[str, Any]]) -> None:
        self.metadata_ = val

    def to_dict(self) -> Dict[str, Any]:
        """Convert message attributes to dictionary."""
        return {
            "id": str(self.id) if self.id else None,
            "session_id": str(self.session_id) if self.session_id else None,
            "message_sequence": self.message_sequence,
            "sender_type": self.sender_type,
            "message_content": self.message_content,
            "message_type": self.message_type,
            "metadata": self.metadata_,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }
