import pytest
from worker.chunker import estimate_tokens, split_text_recursive, chunk_document_sections, create_adaptive_batches


def test_estimate_tokens():
    text = "The quick brown fox jumps over the lazy dog."
    tokens = estimate_tokens(text)
    assert tokens > 0
    # ~44 chars, ~9 words -> ~11-12 tokens
    assert 8 <= tokens <= 20
    assert estimate_tokens("") == 0


def test_split_text_recursive():
    sample_text = (
        "Paragraph 1 contains some important information.\n\n"
        "Paragraph 2 continues the explanation with more details.\n\n"
        "Paragraph 3 concludes the section with summary."
    )
    # Split with small max chunk size
    chunks = split_text_recursive(sample_text, max_chunk_size=60, overlap=10)
    assert len(chunks) >= 3
    for c in chunks:
        assert len(c) <= 75  # within boundary tolerance


def test_chunk_document_sections():
    sections = [
        {"page_number": 1, "text": "Page one paragraph one.\n\nPage one paragraph two."},
        {"page_number": 2, "text": "Page two single paragraph."},
    ]
    chunks = chunk_document_sections(sections, chunk_size=200, chunk_overlap=30)
    assert len(chunks) >= 2
    assert chunks[0]["page_number"] == 1
    assert chunks[-1]["page_number"] == 2
    assert "estimated_tokens" in chunks[0]
    assert chunks[0]["estimated_tokens"] > 0


def test_create_adaptive_batches():
    # Create 50 small dummy chunks
    chunks = [{"chunk_index": i, "content": f"Chunk number {i} text", "estimated_tokens": 100} for i in range(50)]

    # Batch with max size 10, target tokens 600
    # Each chunk has 100 tokens, so 6 chunks = 600 tokens -> should split around 6 chunks
    batches = create_adaptive_batches(chunks, max_batch_size=10, target_batch_tokens=600)
    assert len(batches) >= 8

    total_chunks_in_batches = sum(len(b) for b in batches)
    assert total_chunks_in_batches == 50

    for b in batches:
        batch_tokens = sum(c["estimated_tokens"] for c in b)
        # Should not wildly exceed target tokens
        assert batch_tokens <= 700
        assert len(b) <= 10
