"""Runtime configuration, loaded from environment variables or a local .env file."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    alarm_api_base_url: HttpUrl = HttpUrl("http://localhost:8000")
    alarm_api_token: SecretStr = SecretStr("")
    alarm_api_timeout_seconds: float = Field(default=10.0, gt=0)
    alarm_api_max_retries: int = Field(default=2, ge=0, le=5)
    alarm_api_client_id: str = "alarm-mcp-server"

    mcp_transport: Literal["stdio", "streamable-http"] = "stdio"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8100

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
