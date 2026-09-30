"""Base declarative model and reusable mixins for SQLAlchemy 2.0 ORM."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy ORM models."""

    def to_dict(self) -> dict[str, Any]:
        """Convert model column attributes to dictionary."""
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self) -> str:
        attrs = []
        for key in self.__mapper__.column_attrs.keys():
            if key in ("id", "name", "title", "status", "alarm_id", "ticket_id", "role"):
                attrs.append(f"{key}={getattr(self, key)!r}")
        attr_str = ", ".join(attrs) if attrs else f"id={getattr(self, 'id', None)!r}"
        return f"<{self.__class__.__name__}({attr_str})>"


class TimestampMixin:
    """Mixin that adds created_at and updated_at UTC timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class UUIDPrimaryKeyMixin:
    """Mixin that adds a UUID v4 string primary key."""

    id: Mapped[str] = mapped_column(
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False,
    )
