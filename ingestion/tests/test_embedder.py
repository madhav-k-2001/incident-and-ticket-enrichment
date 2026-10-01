import math
import pytest
from app.services.embedder import EmbedderService, generate_mock_embedding


def test_mock_embedding_properties():
    text = "Artificial intelligence and vector databases"
    vec = generate_mock_embedding(text, dimension=768)

    assert len(vec) == 768
    # Test L2 normalization: sum(x^2) should be approximately 1.0
    l2_norm = math.sqrt(sum(x * x for x in vec))
    assert pytest.approx(l2_norm, 0.001) == 1.0

    # Test determinism
    vec2 = generate_mock_embedding(text, dimension=768)
    assert vec == vec2

    # Different text gives different vector
    vec_diff = generate_mock_embedding("A completely unrelated topic", dimension=768)
    assert vec != vec_diff


def test_embedder_service_mock_mode():
    service = EmbedderService(rate_limiter=None)
    texts = ["First document paragraph", "Second document paragraph with different words"]
    embeddings = service.embed_texts(texts)
    assert len(embeddings) == 2
    assert len(embeddings[0]) == 768
    assert len(embeddings[1]) == 768

    query_emb = service.embed_query("Search query string")
    assert len(query_emb) == 768
