"""Chat history persistence via the Agents SDK ``SQLAlchemySession`` (PostgreSQL)."""

from __future__ import annotations

from agents.extensions.memory import SQLAlchemySession
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from apps.backend.app.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """One shared async engine (connection pool) for the whole app."""
    return create_async_engine(
        settings.database_url,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT,
        pool_recycle=settings.DB_POOL_RECYCLE,
        pool_pre_ping=settings.DB_POOL_PRE_PING,
        echo=settings.DB_ECHO,
    )


def get_chat_session(session_id: str, engine: AsyncEngine, *, create_tables: bool = False) -> SQLAlchemySession:
    """History for one conversation; pass it to ``AgentService.run(session=...)``."""
    return SQLAlchemySession(session_id, engine=engine, create_tables=create_tables)
