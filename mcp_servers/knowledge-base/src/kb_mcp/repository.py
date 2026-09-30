"""Read-only data access to the pgvector tables written by the ingestion worker.

Owns connection pooling, SQL and error mapping. Returns plain dicts; knows
nothing about MCP. Schema (see `ingestion/app/db/models.py`):

    documents(id, filename, file_type, file_size, file_path, status,
              total_chunks, processed_chunks, error_message, created_at, updated_at)
    document_chunks(id, document_id -> documents.id, chunk_index, page_number,
                    content, char_count, estimated_tokens, embedding vector(768), created_at)
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, Protocol

import asyncpg

from kb_mcp.config import Settings
from kb_mcp.errors import InvalidRequestError, KnowledgeBaseError, UnavailableError

logger = logging.getLogger(__name__)

Row = dict[str, Any]

# Never select file_path: it is a server-side filesystem path, not useful to callers.
DOCUMENT_COLUMNS = """
    d.id, d.filename, d.file_type, d.file_size, d.status, d.total_chunks,
    d.processed_chunks, d.error_message, d.created_at, d.updated_at
"""
CHUNK_COLUMNS = """
    dc.id AS chunk_id, dc.document_id, d.filename, d.file_type, dc.chunk_index,
    dc.page_number, dc.content, dc.char_count, dc.estimated_tokens
"""


class Repository(Protocol):
    async def list_documents(
        self, *, status: str | None, filename_contains: str | None, limit: int, offset: int
    ) -> tuple[list[Row], int]: ...

    async def get_document(self, document_id: str) -> Row | None: ...

    async def get_chunks(self, document_id: str, *, first: int, last: int) -> list[Row]: ...

    async def semantic_search(
        self, vector: Sequence[float], *, limit: int, document_ids: list[str] | None, filename_contains: str | None
    ) -> list[Row]: ...

    async def keyword_search(
        self, query: str, *, limit: int, document_ids: list[str] | None, filename_contains: str | None
    ) -> list[Row]: ...

    async def stats(self) -> Row: ...


class PgVectorRepository:
    """asyncpg-backed repository. The pool is created lazily so the MCP server can
    start (and report a clear error) even while the database is still coming up."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # ---- Documents ----------------------------------------------------------

    async def list_documents(
        self, *, status: str | None, filename_contains: str | None, limit: int, offset: int
    ) -> tuple[list[Row], int]:
        where, params = _filters(status=status, filename_contains=filename_contains)
        n = len(params)
        rows = await self._fetch(
            f"""
            SELECT {DOCUMENT_COLUMNS}, count(*) OVER () AS total
            FROM documents d
            {where}
            ORDER BY d.filename ASC, d.created_at DESC
            LIMIT ${n + 1} OFFSET ${n + 2}
            """,
            *params,
            limit,
            offset,
        )
        if rows:
            return [_without(r, "total") for r in rows], rows[0]["total"]
        # Page is past the end (or no matches); still report the true total.
        total = await self._fetchval(f"SELECT count(*) FROM documents d {where}", *params)
        return [], total

    async def get_document(self, document_id: str) -> Row | None:
        rows = await self._fetch(f"SELECT {DOCUMENT_COLUMNS} FROM documents d WHERE d.id = $1", document_id)
        return rows[0] if rows else None

    async def get_chunks(self, document_id: str, *, first: int, last: int) -> list[Row]:
        return await self._fetch(
            f"""
            SELECT {CHUNK_COLUMNS}
            FROM document_chunks dc JOIN documents d ON d.id = dc.document_id
            WHERE dc.document_id = $1 AND dc.chunk_index BETWEEN $2 AND $3
            ORDER BY dc.chunk_index ASC
            """,
            document_id,
            first,
            last,
        )

    # ---- Search -------------------------------------------------------------

    async def semantic_search(
        self, vector: Sequence[float], *, limit: int, document_ids: list[str] | None, filename_contains: str | None
    ) -> list[Row]:
        where, params = _filters(document_ids=document_ids, filename_contains=filename_contains, first_param=2)
        # `<=>` is cosine distance, served by the HNSW vector_cosine_ops index.
        return await self._fetch(
            f"""
            SELECT {CHUNK_COLUMNS}, 1 - (dc.embedding <=> $1::vector) AS similarity
            FROM document_chunks dc JOIN documents d ON d.id = dc.document_id
            {where}
            ORDER BY dc.embedding <=> $1::vector ASC
            LIMIT ${len(params) + 2}
            """,
            _vector_literal(vector),
            *params,
            limit,
        )

    async def keyword_search(
        self, query: str, *, limit: int, document_ids: list[str] | None, filename_contains: str | None
    ) -> list[Row]:
        where, params = _filters(document_ids=document_ids, filename_contains=filename_contains, first_param=2)
        where = f"{where} AND" if where else "WHERE"
        return await self._fetch(
            f"""
            -- OR the query terms (phrases from hyphenated words are kept) so one missing
            -- word doesn't empty the result; ts_rank_cd still favours chunks matching more terms.
            WITH q AS (SELECT to_tsquery('english', replace(plainto_tsquery('english', $1)::text, ' & ', ' | ')) AS tsq)
            SELECT {CHUNK_COLUMNS}, ts_rank_cd(to_tsvector('english', dc.content), q.tsq) AS keyword_rank
            FROM document_chunks dc JOIN documents d ON d.id = dc.document_id, q
            {where} to_tsvector('english', dc.content) @@ q.tsq
            ORDER BY keyword_rank DESC, dc.document_id, dc.chunk_index
            LIMIT ${len(params) + 2}
            """,
            query,
            *params,
            limit,
        )

    # ---- Health -------------------------------------------------------------

    async def stats(self) -> Row:
        status_rows = await self._fetch("SELECT status, count(*) AS n FROM documents GROUP BY status")
        chunks = await self._fetchval("SELECT count(*) FROM document_chunks")
        # For the pgvector `vector` type, atttypmod holds the declared dimension.
        dimension = await self._fetchval(
            """
            SELECT a.atttypmod FROM pg_attribute a
            WHERE a.attrelid = 'document_chunks'::regclass AND a.attname = 'embedding'
            """
        )
        return {
            "documents_by_status": {r["status"]: r["n"] for r in status_rows},
            "total_chunks": chunks,
            "stored_embedding_dimension": dimension if dimension and dimension > 0 else None,
        }

    # ---- Transport ----------------------------------------------------------

    async def _fetch(self, sql: str, *args: Any) -> list[Row]:
        async with self._connection() as conn:
            return [dict(r) for r in await conn.fetch(sql, *args)]

    async def _fetchval(self, sql: str, *args: Any) -> Any:
        async with self._connection() as conn:
            return await conn.fetchval(sql, *args)

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[asyncpg.Connection]:
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                yield conn
        except KnowledgeBaseError:
            raise
        except asyncpg.UndefinedTableError as exc:
            raise UnavailableError(
                "The knowledge base tables do not exist yet. Start the ingestion service to initialise them."
            ) from exc
        except asyncpg.DataError as exc:
            # e.g. "different vector dimensions" when EMBEDDING_DIMENSION disagrees with the stored vectors
            raise InvalidRequestError(f"The knowledge base rejected the query: {exc}") from exc
        except (OSError, TimeoutError, asyncpg.PostgresConnectionError, asyncpg.InterfaceError) as exc:
            logger.error("database_unavailable", extra={"fields": {"error": type(exc).__name__}})
            raise UnavailableError("The knowledge base database is unreachable or timed out.") from exc
        except asyncpg.PostgresError as exc:
            logger.error("database_error", extra={"fields": {"error": type(exc).__name__, "detail": str(exc)}})
            raise UnavailableError("The knowledge base database failed to run the query.") from exc

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    self._pool = await asyncpg.create_pool(
                        dsn=self._settings.dsn,
                        min_size=0,
                        max_size=self._settings.db_pool_max_size,
                        command_timeout=self._settings.db_command_timeout_seconds,
                        timeout=self._settings.db_command_timeout_seconds,
                    )
        return self._pool


# ---- Helpers ----------------------------------------------------------------


def _filters(
    *,
    status: str | None = None,
    document_ids: list[str] | None = None,
    filename_contains: str | None = None,
    first_param: int = 1,
) -> tuple[str, list[Any]]:
    """Build a WHERE clause over `d`/`dc` with numbered asyncpg placeholders."""
    clauses: list[str] = []
    params: list[Any] = []

    def add(template: str, value: Any) -> None:
        params.append(value)
        clauses.append(template.format(f"${first_param + len(params) - 1}"))

    if status:
        add("d.status = {}", status)
    if document_ids:
        add("d.id = ANY({}::text[])", document_ids)
    if filename_contains:
        add("d.filename ILIKE {} ESCAPE '\\'", f"%{_escape_like(filename_contains)}%")
    return ("WHERE " + " AND ".join(clauses) if clauses else ""), params


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vector) + "]"


def _without(row: Row, key: str) -> Row:
    return {k: v for k, v in row.items() if k != key}
