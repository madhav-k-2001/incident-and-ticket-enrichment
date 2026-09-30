from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from pathlib import Path


class Settings(BaseSettings):
    # Gemini API Configuration
    GEMINI_API_KEY: str = ""
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-2-preview"
    EMBEDDING_DIMENSION: int = 768

    # Strict Rate Limit Settings (100 RPM, 30K TPM, 1K RPD)
    RATE_LIMIT_MAX_RPM: int = 80
    RATE_LIMIT_MAX_TPM: int = 24000
    RATE_LIMIT_MAX_RPD: int = 950

    # PostgreSQL Database
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "ingestion_db"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5434

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6380
    REDIS_QUEUE_NAME: str = "ingestion:queue"

    # Ingestion & Chunking
    UPLOAD_DIR: str = "./uploads"
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 150
    MAX_BATCH_SIZE: int = 30
    TARGET_BATCH_TOKENS: int = 6000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def database_url(self) -> str:
        return f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @property
    def redis_url(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    @property
    def upload_path(self) -> Path:
        path = Path(self.UPLOAD_DIR)
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache()
def get_settings() -> Settings:
    return Settings()
