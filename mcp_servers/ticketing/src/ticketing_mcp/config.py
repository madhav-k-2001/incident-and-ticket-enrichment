"""Runtime configuration, loaded from environment variables or a local .env file."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ticketing_api_base_url: HttpUrl = HttpUrl("http://localhost:8000")
    ticketing_api_token: SecretStr = SecretStr("")
    ticketing_timeout_seconds: float = Field(default=10.0, gt=0)
    ticketing_max_retries: int = Field(default=2, ge=0)
    ticketing_retry_backoff_seconds: float = Field(default=0.5, ge=0)
    ticketing_client_id: str = "ticketing-mcp"

    mcp_transport: Literal["stdio", "streamable-http"] = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8100

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
