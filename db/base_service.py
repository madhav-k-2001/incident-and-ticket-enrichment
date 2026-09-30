"""Base service class providing seamless database session access to domain services."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional, Union

from sqlalchemy.ext.asyncio import AsyncSession

from db.client import DatabaseClient
from db.session import get_default_client


class BaseDatabaseService:
    """
    Base service class for domain services accessing the database.
    
    Supports both:
    1. Direct DatabaseClient composition (sessions created per operation / transaction).
    2. Request-scoped AsyncSession injection (re-using an existing transactional session).
    
    Usage:
        class ChatService(BaseDatabaseService):
            async def add_message(self, session_id: str, content: str):
                async with self.get_session() as session:
                    msg = ChatMessage(session_id=session_id, role="user", content=content)
                    session.add(msg)
                    return msg
    """

    def __init__(
        self,
        db: Optional[Union[DatabaseClient, AsyncSession]] = None,
    ) -> None:
        if isinstance(db, AsyncSession):
            self._session: Optional[AsyncSession] = db
            self._client: Optional[DatabaseClient] = None
        else:
            self._session = None
            self._client = db or get_default_client()

    @property
    def client(self) -> DatabaseClient:
        """Return the backing DatabaseClient."""
        if self._client is None:
            return get_default_client()
        return self._client

    @asynccontextmanager
    async def get_session(self) -> AsyncIterator[AsyncSession]:
        """
        Yield an AsyncSession.
        
        If an AsyncSession was injected during construction, yields that session directly.
        Otherwise, borrows a new transactional session from the DatabaseClient context manager.
        """
        if self._session is not None:
            yield self._session
        else:
            async with self.client.session() as session:
                yield session
