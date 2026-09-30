"""Database configuration module for PostgreSQL / SQLAlchemy client."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Optional
from urllib.parse import urlparse, urlunparse


@dataclass
class DatabaseConfig:
    """Configuration settings for PostgreSQL database connection and connection pool."""

    host: str = field(
        default_factory=lambda: os.getenv("POSTGRES_HOST", "localhost")
    )
    port: int = field(
        default_factory=lambda: int(os.getenv("POSTGRES_PORT", "5432"))
    )
    user: str = field(
        default_factory=lambda: os.getenv("POSTGRES_USER", "postgres")
    )
    password: str = field(
        default_factory=lambda: os.getenv("POSTGRES_PASSWORD", "")
    )
    database: str = field(
        default_factory=lambda: os.getenv("POSTGRES_DB", "postgres")
    )
    ssl_mode: Optional[str] = field(
        default_factory=lambda: os.getenv("POSTGRES_SSLMODE", None)
    )
    database_url: Optional[str] = field(
        default_factory=lambda: os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    )

    # Connection pool configuration
    pool_size: int = field(
        default_factory=lambda: int(os.getenv("DB_POOL_SIZE", "10"))
    )
    max_overflow: int = field(
        default_factory=lambda: int(os.getenv("DB_MAX_OVERFLOW", "20"))
    )
    pool_timeout: float = field(
        default_factory=lambda: float(os.getenv("DB_POOL_TIMEOUT", "30.0"))
    )
    pool_recycle: int = field(
        default_factory=lambda: int(os.getenv("DB_POOL_RECYCLE", "1800"))
    )
    pool_pre_ping: bool = field(
        default_factory=lambda: os.getenv("DB_POOL_PRE_PING", "true").lower() in ("true", "1", "yes")
    )
    echo: bool = field(
        default_factory=lambda: os.getenv("DB_ECHO", "false").lower() in ("true", "1", "yes")
    )
    embedding_dimension: int = field(
        default_factory=lambda: int(os.getenv("EMBEDDING_DIMENSION", "768"))
    )

    @property
    def EMBEDDING_DIMENSION(self) -> int:
        """Alias for embedding dimension compatibility with ingestion settings."""
        return self.embedding_dimension


    @property
    def async_url(self) -> str:
        """
        Return normalized async database URL with an async driver prefix.
        
        Converts:
          - postgresql:// -> postgresql+asyncpg://
          - postgres://   -> postgresql+asyncpg://
          - sqlite:///    -> sqlite+aiosqlite:///
        """
        if self.database_url:
            raw_url = self.database_url
            if raw_url.startswith("postgresql://"):
                return raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            if raw_url.startswith("postgres://"):
                return raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
            if raw_url.startswith("sqlite://") and not raw_url.startswith("sqlite+aiosqlite://"):
                return raw_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
            return raw_url

        auth = f"{self.user}:{self.password}" if self.password else self.user
        netloc = f"{auth}@{self.host}:{self.port}"
        url = f"postgresql+asyncpg://{netloc}/{self.database}"

        if self.ssl_mode:
            url += f"?ssl={self.ssl_mode}"
        return url

    @property
    def sync_url(self) -> str:
        """Return synchronous database URL."""
        if self.database_url:
            raw_url = self.database_url
            if raw_url.startswith("postgresql+asyncpg://"):
                return raw_url.replace("postgresql+asyncpg://", "postgresql://", 1)
            if raw_url.startswith("sqlite+aiosqlite://"):
                return raw_url.replace("sqlite+aiosqlite://", "sqlite://", 1)
            return raw_url

        auth = f"{self.user}:{self.password}" if self.password else self.user
        netloc = f"{auth}@{self.host}:{self.port}"
        url = f"postgresql://{netloc}/{self.database}"

        if self.ssl_mode:
            url += f"?sslmode={self.ssl_mode}"
        return url

    def masked_url(self) -> str:
        """Return safe URL string with password masked."""
        try:
            parsed = urlparse(self.async_url)
            if parsed.password:
                netloc = f"{parsed.username}:***@{parsed.hostname}"
                if parsed.port:
                    netloc += f":{parsed.port}"
                return urlunparse(parsed._replace(netloc=netloc))
            return self.async_url
        except Exception:
            return "postgresql+asyncpg://***"

    def __repr__(self) -> str:
        return (
            f"DatabaseConfig("
            f"url={self.masked_url()!r}, "
            f"pool_size={self.pool_size}, "
            f"max_overflow={self.max_overflow}, "
            f"pool_timeout={self.pool_timeout}, "
            f"pool_recycle={self.pool_recycle}, "
            f"pool_pre_ping={self.pool_pre_ping}, "
            f"echo={self.echo})"
        )


_global_settings: Optional[DatabaseConfig] = None


def get_settings() -> DatabaseConfig:
    """Return cached or new DatabaseConfig instance for settings compatibility."""
    global _global_settings
    if _global_settings is None:
        _global_settings = DatabaseConfig()
    return _global_settings

