"""Query embedding, kept byte-compatible with the ingestion pipeline.

Search only works if queries are embedded exactly like the stored chunks were:
same model, same output dimension, same request shape. When no Gemini key is
configured the ingestion worker stores deterministic mock vectors, so this module
reproduces that mock too (see `ingestion/app/services/embedder.py`).
"""

import asyncio
import hashlib
import logging
import math
import random
from typing import Literal, Protocol

from kb_mcp.config import Settings
from kb_mcp.errors import EmbeddingError, UnavailableError

logger = logging.getLogger(__name__)

EmbeddingMode = Literal["gemini", "mock"]


class QueryEmbedder(Protocol):
    mode: EmbeddingMode

    async def embed_query(self, text: str) -> list[float]: ...


def mock_embedding(text: str, dimension: int) -> list[float]:
    """Deterministic unit vector seeded by the text hash. Mirrors the ingestion mock."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    vector = [rng.gauss(0, 1) for _ in range(dimension)]
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm > 0 else vector


class MockEmbedder:
    mode: EmbeddingMode = "mock"

    def __init__(self, dimension: int) -> None:
        self._dimension = dimension

    async def embed_query(self, text: str) -> list[float]:
        return mock_embedding(text, self._dimension)


class GeminiEmbedder:
    mode: EmbeddingMode = "gemini"

    def __init__(self, settings: Settings) -> None:
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
        self._model = settings.gemini_embedding_model
        self._dimension = settings.embedding_dimension
        self._timeout = settings.embedding_timeout_seconds
        self._max_retries = settings.embedding_max_retries

    async def embed_query(self, text: str) -> list[float]:
        types = self._types
        # Same request shape as the ingestion worker: a list of Content objects.
        contents = [types.Content(parts=[types.Part.from_text(text=text)])]
        config = types.EmbedContentConfig(output_dimensionality=self._dimension)

        for attempt in range(1, self._max_retries + 2):
            try:
                response = await asyncio.wait_for(
                    self._client.aio.models.embed_content(model=self._model, contents=contents, config=config),
                    timeout=self._timeout,
                )
            except Exception as exc:  # SDK raises a variety of transport/API errors
                if attempt > self._max_retries:
                    logger.error("embedding_failed", extra={"fields": {"attempt": attempt, "error": type(exc).__name__}})
                    raise UnavailableError("The embedding service is unreachable or rejected the request.") from exc
                logger.warning("embedding_retry", extra={"fields": {"attempt": attempt, "error": type(exc).__name__}})
                await asyncio.sleep(0.5 * 2 ** (attempt - 1))
                continue

            embeddings = getattr(response, "embeddings", None) or []
            if not embeddings or not embeddings[0].values:
                raise EmbeddingError("The embedding service returned no vector for the query.")
            return list(embeddings[0].values)

        raise EmbeddingError("Query embedding was not attempted.")


def create_embedder(settings: Settings) -> QueryEmbedder:
    if settings.gemini_configured:
        return GeminiEmbedder(settings)
    logger.warning("gemini_api_key_missing_using_mock_embeddings")
    return MockEmbedder(settings.embedding_dimension)
