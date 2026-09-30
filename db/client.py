"""SQLAlchemy Database Client for PostgreSQL and Async Applications."""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
import logging
import time
from typing import Any, AsyncIterator, Dict, Iterator, Optional

from sqlalchemy import create_engine, Engine, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from db.config import DatabaseConfig
from db.models.base import Base

logger = logging.getLogger("db.client")


class DatabaseClient:
    """
    SQLAlchemy Client managing connection pooling, sessions, and database lifecycle.
    
    Primary interface is asynchronous (`AsyncSession`), with optional synchronous session
    helpers for migrations, CLI tooling, and synchronous workers.
    """

    def __init__(
        self,
        config: Optional[DatabaseConfig] = None,
        async_engine: Optional[AsyncEngine] = None,
        sync_engine: Optional[Engine] = None,
    ) -> None:
        self.config = config or DatabaseConfig()
        self._async_engine = async_engine
        self._sync_engine = sync_engine
        self._async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
        self._sync_session_factory: Optional[sessionmaker[Session]] = None

    @property
    def engine(self) -> AsyncEngine:
        """Return or lazily initialize the AsyncEngine instance."""
        if self._async_engine is None:
            url = self.config.async_url
            kwargs: Dict[str, Any] = {
                "echo": self.config.echo,
            }

            # SQLite (used in unit tests) does not support standard PostgreSQL pool parameters
            if url.startswith("sqlite"):
                kwargs["pool_pre_ping"] = self.config.pool_pre_ping
            else:
                kwargs.update({
                    "pool_size": self.config.pool_size,
                    "max_overflow": self.config.max_overflow,
                    "pool_timeout": self.config.pool_timeout,
                    "pool_recycle": self.config.pool_recycle,
                    "pool_pre_ping": self.config.pool_pre_ping,
                })

            self._async_engine = create_async_engine(url, **kwargs)
            logger.info("Initialized AsyncEngine with target: %s", self.config.masked_url())
        return self._async_engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Return or lazily initialize the async sessionmaker."""
        if self._async_session_factory is None:
            self._async_session_factory = async_sessionmaker(
                bind=self.engine,
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,
            )
        return self._async_session_factory

    @property
    def sync_engine(self) -> Engine:
        """Return or lazily initialize the synchronous Engine instance."""
        if self._sync_engine is None:
            url = self.config.sync_url
            kwargs: Dict[str, Any] = {
                "echo": self.config.echo,
            }
            if not url.startswith("sqlite"):
                kwargs.update({
                    "pool_size": self.config.pool_size,
                    "max_overflow": self.config.max_overflow,
                    "pool_timeout": self.config.pool_timeout,
                    "pool_recycle": self.config.pool_recycle,
                    "pool_pre_ping": self.config.pool_pre_ping,
                })
            else:
                kwargs["pool_pre_ping"] = self.config.pool_pre_ping

            self._sync_engine = create_engine(url, **kwargs)
        return self._sync_engine

    @property
    def sync_session_factory(self) -> sessionmaker[Session]:
        """Return or lazily initialize the synchronous sessionmaker."""
        if self._sync_session_factory is None:
            self._sync_session_factory = sessionmaker(
                bind=self.sync_engine,
                class_=Session,
                expire_on_commit=False,
                autoflush=False,
            )
        return self._sync_session_factory

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """
        Async context manager providing a transactional AsyncSession.
        
        Automatically commits changes on clean exit, rolls back on any uncaught
        exception, and always closes the session in the finally block.
        """
        session_instance: AsyncSession = self.session_factory()
        try:
            yield session_instance
            await session_instance.commit()
        except Exception:
            await session_instance.rollback()
            raise
        finally:
            await session_instance.close()

    @contextmanager
    def sync_session(self) -> Iterator[Session]:
        """Synchronous context manager providing a transactional Session."""
        session_instance: Session = self.sync_session_factory()
        try:
            yield session_instance
            session_instance.commit()
        except Exception:
            session_instance.rollback()
            raise
        finally:
            session_instance.close()

    async def ping(self) -> bool:
        """Verify database connectivity by executing 'SELECT 1'."""
        try:
            async with self.engine.connect() as conn:
                result = await conn.execute(text("SELECT 1"))
                return result.scalar() == 1
        except Exception as err:
            logger.warning("Database ping failed: %s", err)
            return False

    async def check_health(self) -> Dict[str, Any]:
        """Return comprehensive connectivity, latency, and status diagnostics."""
        start_time = time.perf_counter()
        is_alive = await self.ping()
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)

        return {
            "status": "healthy" if is_alive else "unhealthy",
            "connected": is_alive,
            "latency_ms": latency_ms if is_alive else None,
            "target": self.config.masked_url(),
            "pool": {
                "size": getattr(self.engine.pool, "size", lambda: None)(),
                "checked_in": getattr(self.engine.pool, "checkedin", lambda: None)(),
                "checked_out": getattr(self.engine.pool, "checkedout", lambda: None)(),
                "overflow": getattr(self.engine.pool, "overflow", lambda: None)(),
            },
        }

    async def create_tables(self) -> None:
        """Create all tables registered in metadata (useful for dev/test setups)."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables verified / created.")

    async def drop_tables(self) -> None:
        """Drop all tables registered in metadata (useful for test tear-downs)."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        logger.info("Database tables dropped.")

    async def disconnect(self) -> None:
        """Dispose of the async engine and its connection pools."""
        if self._async_engine is not None:
            await self._async_engine.dispose()
            self._async_engine = None
            self._async_session_factory = None
            logger.info("Async engine connection pool disposed.")
        if self._sync_engine is not None:
            self._sync_engine.dispose()
            self._sync_engine = None
            self._sync_session_factory = None
            logger.info("Sync engine connection pool disposed.")

    async def aclose(self) -> None:
        """Alias for disconnect."""
        await self.disconnect()

    async def __aenter__(self) -> "DatabaseClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.disconnect()
