"""Backend API settings, read from environment variables / ``.env``."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Incident and Ticket Enrichment API"
    APP_VERSION: str = "0.1.0"
    LOG_LEVEL: str = "INFO"

    # Agent
    AGENT_NAME: str = "Assistant"
    AGENT_MODEL: Optional[str] = None
    AGENT_MAX_TURNS: int = 10

    # MCP servers: path to a JSON file or an inline JSON string following the
    # schema documented in common.load_mcp_service.MCPServerConfigService.
    MCP_SERVERS_CONFIG: Optional[str] = None

    # PostgreSQL (chat history). DATABASE_URL wins over the POSTGRES_* parts.
    DATABASE_URL: Optional[str] = None
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "copilot_db"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: float = 30.0
    DB_POOL_RECYCLE: int = 1800
    DB_POOL_PRE_PING: bool = True
    DB_ECHO: bool = False

    # Create the chat history tables on startup (dev convenience)
    DB_CREATE_TABLES: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"postgresql+asyncpg://{quote_plus(self.POSTGRES_USER)}:{quote_plus(self.POSTGRES_PASSWORD)}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    def mcp_servers_spec(self) -> dict[str, Any]:
        """Return the parsed MCP server spec, or an empty spec if none is set."""
        raw = (self.MCP_SERVERS_CONFIG or "").strip()
        if not raw:
            return {"servers": []}
        if raw[0] in "{[":
            return json.loads(raw)
        path = Path(raw)
        if not path.is_file():
            raise FileNotFoundError(f"MCP_SERVERS_CONFIG file not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))


@lru_cache()
def get_settings() -> Settings:
    return Settings()
