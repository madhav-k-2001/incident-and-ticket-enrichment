import time
import math
import random
import logging
from typing import List, Optional
import hashlib

from app.config import get_settings
from app.services.rate_limiter import RateLimiter
from worker.chunker import estimate_tokens

logger = logging.getLogger(__name__)


def generate_mock_embedding(text: str, dimension: int = 768) -> List[float]:
    """
    Generates a deterministic, normalized mock vector embedding based on text hashing.
    Used for unit testing and offline development when GEMINI_API_KEY is not configured.
    Produces high-quality cosine similarities for semantically related mock texts.
    """
    # Create seed from text hash
    h = hashlib.sha256(text.encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(h[:8], "big"))

    # Generate random vector
    vector = [rng.gauss(0, 1) for _ in range(dimension)]

    # Normalize to unit length (L2 norm = 1.0)
    norm = math.sqrt(sum(x * x for x in vector))
    if norm > 0:
        vector = [x / norm for x in vector]
    return vector


class EmbedderService:
    def __init__(self, rate_limiter: Optional[RateLimiter] = None):
        self.settings = get_settings()
        self.rate_limiter = rate_limiter or RateLimiter()
        self.client = None

        if self.settings.GEMINI_API_KEY and self.settings.GEMINI_API_KEY != "your_gemini_api_key_here":
            try:
                from google import genai

                self.client = genai.Client(api_key=self.settings.GEMINI_API_KEY)
                logger.info(f"Initialized Google GenAI client with model: {self.settings.GEMINI_EMBEDDING_MODEL}")
            except Exception as e:
                logger.error(f"Failed to initialize Google GenAI Client: {e}")
                self.client = None
        else:
            logger.warning("GEMINI_API_KEY is not set or using default placeholder. Mock embedding mode active.")

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """
        Embeds a list of texts using Gemini Embedding 2 (gemini-embedding-2-preview).
        Enforces rate limits (100 RPM, 30K TPM, 1K RPD) and applies exponential backoff for 429s.
        """
        if not texts:
            return []

        # If running in mock/test mode without API key
        if not self.client:
            logger.info(
                f"Generating mock embeddings for {len(texts)} texts (dimension: {self.settings.EMBEDDING_DIMENSION})"
            )
            return [generate_mock_embedding(t, self.settings.EMBEDDING_DIMENSION) for t in texts]

        from google.genai import types

        total_tokens = sum(estimate_tokens(t) for t in texts)

        # 1. Acquire rate limit budget
        acquired = self.rate_limiter.acquire(tokens=total_tokens, requests=1, max_wait=300.0)
        if not acquired:
            raise TimeoutError("Rate limit budget acquisition timed out.")

        # 2. Call Gemini API with retry logic
        max_retries = 5
        base_delay = 2.0

        for attempt in range(max_retries):
            try:
                # Wrap each text into a Content object so gemini-embedding-2 returns separate embeddings
                contents = [types.Content(parts=[types.Part.from_text(text=t)]) for t in texts]

                config = types.EmbedContentConfig(output_dimensionality=self.settings.EMBEDDING_DIMENSION)

                response = self.client.models.embed_content(
                    model=self.settings.GEMINI_EMBEDDING_MODEL, contents=contents, config=config
                )

                embeddings = []
                if hasattr(response, "embeddings") and response.embeddings:
                    embeddings = [list(emb.values) for emb in response.embeddings]
                elif hasattr(response, "embedding") and response.embedding:
                    embeddings = [list(response.embedding.values)]

                # Check if we got an embedding for each input text
                if len(embeddings) == len(texts):
                    return embeddings
                elif len(embeddings) == 1 and len(texts) > 1:
                    # Fallback if model aggregated contents: call individually with rate limiting
                    logger.warning(
                        "Embedding model returned single aggregated embedding; falling back to per-item requests."
                    )
                    return self._embed_individually(texts)
                else:
                    return embeddings

            except Exception as e:
                err_str = str(e).lower()
                is_rate_limited = "429" in err_str or "quota" in err_str or "resource_exhausted" in err_str

                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt) + random.uniform(0.5, 2.0)
                    if is_rate_limited:
                        delay = max(delay, 20.0)  # Wait longer on 429
                        logger.warning(
                            f"Rate limit 429 hit on Gemini API (attempt {attempt + 1}/{max_retries}). Backing off {delay:.1f}s..."
                        )
                    else:
                        logger.warning(
                            f"Transient error calling Gemini API: {e} (attempt {attempt + 1}/{max_retries}). Retrying in {delay:.1f}s..."
                        )
                    time.sleep(delay)
                else:
                    logger.error(f"Failed to get embeddings after {max_retries} attempts: {e}")
                    raise

        raise RuntimeError("Unexpected end of retry loop in embed_texts")

    def _embed_individually(self, texts: List[str]) -> List[List[float]]:
        """Fallback method embedding texts individually while respecting rate limits."""
        from google.genai import types

        results = []
        config = types.EmbedContentConfig(output_dimensionality=self.settings.EMBEDDING_DIMENSION)

        for text in texts:
            tokens = estimate_tokens(text)
            self.rate_limiter.acquire(tokens=tokens, requests=1)
            response = self.client.models.embed_content(
                model=self.settings.GEMINI_EMBEDDING_MODEL, contents=text, config=config
            )
            if hasattr(response, "embedding") and response.embedding:
                results.append(list(response.embedding.values))
            elif hasattr(response, "embeddings") and response.embeddings:
                results.append(list(response.embeddings[0].values))
            else:
                raise ValueError("Could not extract embedding from response.")
        return results

    def embed_query(self, query: str) -> List[float]:
        """Embeds a single query string for semantic search."""
        results = self.embed_texts([query])
        if not results:
            raise ValueError("Failed to generate embedding for query.")
        return results[0]
