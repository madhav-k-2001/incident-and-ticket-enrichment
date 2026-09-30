import math

from kb_mcp.config import Settings
from kb_mcp.embedder import MockEmbedder, create_embedder, mock_embedding
from kb_mcp.models import Chunk, overlap_length, stitch_chunks


def _chunk(index: int, content: str, page: int | None = 1) -> Chunk:
    return Chunk(chunk_id=f"c{index}", document_id="d1", filename="f.pdf", chunk_index=index, page_number=page, content=content)


# Taken verbatim from the ingestion chunker (split_text_recursive, size 300, overlap 60).
PREV = "Sentence number 3 describes step 3 of the procedure. Sentence number 4 describes step 4 of the procedure"
NEXT = "ocedure. Sentence number 4 describes step 4 of the procedure. Sentence number 5 describes step 5."


def test_overlap_length_finds_the_shared_suffix_prefix() -> None:
    assert overlap_length(PREV, NEXT) == len("ocedure. Sentence number 4 describes step 4 of the procedure")


def test_stitch_removes_the_ingestion_overlap() -> None:
    text = stitch_chunks([_chunk(0, PREV), _chunk(1, NEXT)])

    assert text == PREV + ". Sentence number 5 describes step 5."
    assert text.count("Sentence number 4") == 1


def test_stitch_joins_non_overlapping_and_non_adjacent_chunks_with_a_blank_line() -> None:
    assert stitch_chunks([_chunk(0, "Alpha section."), _chunk(1, "Beta section.")]) == "Alpha section.\n\nBeta section."
    # Chunks 0 and 2 are not adjacent, so even a textual overlap must not be merged.
    assert stitch_chunks([_chunk(0, PREV), _chunk(2, NEXT)]) == PREV + "\n\n" + NEXT


def test_stitch_adds_page_markers_on_request() -> None:
    text = stitch_chunks([_chunk(0, "Page one text.", page=1), _chunk(1, "Page two text.", page=2)], page_markers=True)

    assert text == "<!-- page 1 -->\n\nPage one text.\n\n<!-- page 2 -->\n\nPage two text."


def test_chunk_and_document_resource_uris() -> None:
    assert _chunk(3, "x").resource_uri == "kb://documents/d1/chunks/3"


def test_mock_embedding_matches_the_ingestion_pipeline() -> None:
    # Reference values from ingestion/app/services/embedder.py::generate_mock_embedding("compressor surge", 768)
    vector = mock_embedding("compressor surge", 768)

    assert [round(x, 10) for x in vector[:4]] == [-0.0532948618, 0.0802161777, -0.0198630133, 0.0180933742]
    assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0)


def test_embedder_falls_back_to_mock_without_a_real_key() -> None:
    for key in ("", "your_gemini_api_key_here"):
        embedder = create_embedder(Settings(_env_file=None, gemini_api_key=key))
        assert isinstance(embedder, MockEmbedder) and embedder.mode == "mock"


def test_dsn_escapes_credentials() -> None:
    settings = Settings(_env_file=None, postgres_password="p@ss:/word", postgres_host="db", postgres_port=5432)

    assert settings.dsn == "postgresql://postgres:p%40ss%3A%2Fword@db:5432/ingestion_db"
