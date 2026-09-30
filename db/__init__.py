"""PostgreSQL SQLAlchemy database client and models package."""

from __future__ import annotations

from db.base_service import BaseDatabaseService
from db.client import DatabaseClient
from db.config import DatabaseConfig, get_settings
from db.models.base import Base
from db.models.document import Document, DocumentChunk
from db.session import get_db, get_default_client, set_default_client

__all__ = [
    "DatabaseClient",
    "DatabaseConfig",
    "BaseDatabaseService",
    "Base",
    "Document",
    "DocumentChunk",
    "get_db",
    "get_default_client",
    "set_default_client",
    "get_settings",
]

