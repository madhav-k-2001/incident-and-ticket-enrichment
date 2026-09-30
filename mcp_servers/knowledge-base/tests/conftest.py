from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from kb_mcp.config import Settings
from kb_mcp.errors import UnavailableError
from kb_mcp.repository import Row

NOW = datetime(2026, 9, 30, tzinfo=UTC)

SOP_ID = "ae12ee92321c48a7a41373739d41a871"
TSG_ID = "423a7c434a3c4d8784b11db47907c290"
PENDING_ID = "0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f"

# Consecutive chunks carry a 20+ char overlap, as the ingestion chunker produces.
SOP_CHUNKS = [
    "# SOP-CMP-201 Discharge Overpressure\n\nStep 1: confirm the discharge pressure alarm on CMP-201.",
    "pressure alarm on CMP-201. Step 2: check the anti-surge valve position and recycle flow.",
    "position and recycle flow. Step 3: if the valve is stuck, switch to manual and notify control.",
]
TSG_CHUNKS = [
    "# TSG Anti-Surge Valve Diagnostics\n\nA stuck anti-surge valve causes surge on compressors.",
    "Check the positioner air supply and stroke the valve.",
]


def _doc(doc_id: str, filename: str, chunks: list[str], status: str = "COMPLETED") -> Row:
    return {
        "id": doc_id,
        "filename": filename,
        "file_type": "pdf",
        "file_size": 1234,
        "status": status,
        "total_chunks": len(chunks),
        "processed_chunks": len(chunks) if status == "COMPLETED" else 0,
        "error_message": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _chunks(doc: Row, contents: list[str]) -> list[Row]:
    return [
        {
            "chunk_id": f"{doc['id'][:8]}-{i}",
            "document_id": doc["id"],
            "filename": doc["filename"],
            "file_type": doc["file_type"],
            "chunk_index": i,
            "page_number": 1,
            "content": text,
            "char_count": len(text),
            "estimated_tokens": len(text) // 4,
        }
        for i, text in enumerate(contents)
    ]


class FakeRepository:
    """In-memory stand-in for PgVectorRepository. Records calls for assertions."""

    def __init__(self) -> None:
        sop = _doc(SOP_ID, "SOP-CMP-201-Discharge-Overpressure.pdf", SOP_CHUNKS)
        tsg = _doc(TSG_ID, "TSG-CMP-AntiSurge-Valve-Diagnostics.pdf", TSG_CHUNKS)
        pending = _doc(PENDING_ID, "SAF-02-Compressor-Hall.pdf", [], status="PROCESSING")
        self.documents = [sop, tsg, pending]
        self.chunks = _chunks(sop, SOP_CHUNKS) + _chunks(tsg, TSG_CHUNKS)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.unavailable = False
        # Canned rankings: (chunk_id, score)
        self.semantic_ranking = [("ae12ee92-1", 0.82), ("423a7c43-0", 0.74), ("ae12ee92-0", 0.40)]
        self.keyword_ranking = [("423a7c43-0", 0.9), ("ae12ee92-2", 0.5)]
        self.stored_dimension = 768

    def _record(self, name: str, **kwargs: Any) -> None:
        self.calls.append((name, kwargs))
        if self.unavailable:
            raise UnavailableError("The knowledge base database is unreachable or timed out.")

    def _chunk(self, chunk_id: str) -> Row:
        return next(c for c in self.chunks if c["chunk_id"] == chunk_id)

    async def list_documents(
        self, *, status: str | None, filename_contains: str | None, limit: int, offset: int
    ) -> tuple[list[Row], int]:
        self._record("list_documents", status=status, filename_contains=filename_contains, limit=limit, offset=offset)
        docs = [
            d
            for d in self.documents
            if (not status or d["status"] == status)
            and (not filename_contains or filename_contains.lower() in d["filename"].lower())
        ]
        return docs[offset : offset + limit], len(docs)

    async def get_document(self, document_id: str) -> Row | None:
        self._record("get_document", document_id=document_id)
        return next((d for d in self.documents if d["id"] == document_id), None)

    async def get_chunks(self, document_id: str, *, first: int, last: int) -> list[Row]:
        self._record("get_chunks", document_id=document_id, first=first, last=last)
        return [c for c in self.chunks if c["document_id"] == document_id and first <= c["chunk_index"] <= last]

    async def semantic_search(
        self, vector: Sequence[float], *, limit: int, document_ids: list[str] | None, filename_contains: str | None
    ) -> list[Row]:
        self._record("semantic_search", dims=len(vector), limit=limit, document_ids=document_ids)
        rows = [{**self._chunk(cid), "similarity": s} for cid, s in self.semantic_ranking]
        return [r for r in rows if not document_ids or r["document_id"] in document_ids][:limit]

    async def keyword_search(
        self, query: str, *, limit: int, document_ids: list[str] | None, filename_contains: str | None
    ) -> list[Row]:
        self._record("keyword_search", query=query, limit=limit, document_ids=document_ids)
        rows = [{**self._chunk(cid), "keyword_rank": s} for cid, s in self.keyword_ranking]
        return [r for r in rows if not document_ids or r["document_id"] in document_ids][:limit]

    async def stats(self) -> Row:
        self._record("stats")
        by_status: dict[str, int] = {}
        for d in self.documents:
            by_status[d["status"]] = by_status.get(d["status"], 0) + 1
        return {
            "documents_by_status": by_status,
            "total_chunks": len(self.chunks),
            "stored_embedding_dimension": self.stored_dimension,
        }


class FixedEmbedder:
    mode = "gemini"

    def __init__(self, dimension: int = 768) -> None:
        self.dimension = dimension
        self.queries: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.1] * self.dimension


@pytest.fixture
def repo() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def embedder() -> FixedEmbedder:
    return FixedEmbedder()


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, gemini_api_key="", embedding_dimension=768)
