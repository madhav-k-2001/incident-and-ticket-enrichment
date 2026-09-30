"""Runtime configuration, loaded from environment variables or a local .env file.

Database and embedding variables use the same names as the ingestion pipeline
(`ingestion/.env`), so one .env file can configure both.
"""

from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# The ingestion pipeline ships this placeholder in .env.example; treat it as unset.
GEMINI_KEY_PLACEHOLDER = "your_gemini_api_key_here"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # pgvector database written by the ingestion worker
    postgres_user: str = "postgres"
    postgres_password: SecretStr = SecretStr("postgres")
    postgres_db: str = "ingestion_db"
    postgres_host: str = "localhost"
    postgres_port: int = 5434
    db_pool_max_size: int = Field(default=5, ge=1)
    db_command_timeout_seconds: float = Field(default=10.0, gt=0)

    # Query embeddings: must match the model and dimension used at ingestion time
    gemini_api_key: SecretStr = SecretStr("")
    gemini_embedding_model: str = "gemini-embedding-2-preview"
    embedding_dimension: int = Field(default=768, gt=0)
    embedding_timeout_seconds: float = Field(default=15.0, gt=0)
    embedding_max_retries: int = Field(default=2, ge=0)

    # Upper bound on chunks stitched into one full-document resource
    max_document_chunks: int = Field(default=2000, ge=1)

    mcp_transport: Literal["stdio", "streamable-http"] = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8100

    log_level: str = "INFO"

    @property
    def dsn(self) -> str:
        password = quote(self.postgres_password.get_secret_value(), safe="")
        user = quote(self.postgres_user, safe="")
        return f"postgresql://{user}:{password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @property
    def gemini_configured(self) -> bool:
        key = self.gemini_api_key.get_secret_value()
        return bool(key) and key != GEMINI_KEY_PLACEHOLDER


@lru_cache
def get_settings() -> Settings:
    return Settings()
