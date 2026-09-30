"""Session providers and FastAPI dependency helpers."""

from __future__ import annotations

from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from db.client import DatabaseClient

_default_client: Optional[DatabaseClient] = None


def get_default_client() -> DatabaseClient:
    """Return the global default DatabaseClient instance, creating one if not set."""
    global _default_client
    if _default_client is None:
        _default_client = DatabaseClient()
    return _default_client


def set_default_client(client: Optional[DatabaseClient]) -> None:
    """Set or reset the global default DatabaseClient instance."""
    global _default_client
    _default_client = client


async def get_db(
    client: Optional[DatabaseClient] = None,
) -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields a transactional AsyncSession.
    
    Usage:
        @app.get("/items")
        async def read_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    db_client = client or get_default_client()
    async with db_client.session() as session:
        yield session
