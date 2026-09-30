"""MCP interface layer: tools, resources, input contracts and error mapping.

Tools stay thin: validate input (via type hints), bind a trace id, delegate to
KnowledgeBaseService, and translate domain errors into MCP tool errors.

Resources expose the ingested RAG documents directly:
    kb://documents                               catalog of all documents (JSON)
    kb://documents/{document_id}                 full document text (Markdown), one listed per ingested doc
    kb://documents/{document_id}/chunks/{index}  one stored chunk (plain text)
"""

import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError, ToolError
from mcp.types import Resource as MCPResource
from mcp.types import ToolAnnotations
from pydantic import Field, StringConstraints

from kb_mcp.config import Settings
from kb_mcp.embedder import QueryEmbedder, create_embedder
from kb_mcp.errors import KnowledgeBaseError, NotFoundError
from kb_mcp.models import (
    URI_SCHEME,
    DocumentList,
    DocumentPassage,
    DocumentStatus,
    KnowledgeBaseStatus,
    SearchMode,
    SearchResults,
    document_uri,
)
from kb_mcp.observability import bind_trace_id
from kb_mcp.repository import PgVectorRepository, Repository
from kb_mcp.service import KnowledgeBaseService

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
Knowledge base of plant operations documents (SOPs, troubleshooting guides, KB articles,
safety instructions, escalation matrices), stored as embedded chunks in pgvector.
- Start with `search_knowledge_base` using the symptom, alarm, asset id or procedure name.
  mode='hybrid' (default) combines vector similarity with full-text matching and is best for
  queries mixing prose and identifiers such as 'CMP-201' or 'SOP-BFP-101'.
- Expand a hit with `get_chunk_context`, or read a whole document with `read_document`
  (paginate with `next_chunk`).
- `list_documents` shows what is available; `get_knowledge_base_status` reports ingestion
  progress and configuration problems.
- Cite documents by filename and page. Every document is also a resource at kb://documents/{id}.
All tools are read-only. Pass `trace_id` in the request `_meta` to correlate calls.
"""

# ---- Shared parameter types -------------------------------------------------

# Ingestion ids are uuid4 hex (32 chars) or hyphenated uuids (36).
DOCUMENT_ID_PATTERN = r"^[A-Za-z0-9-]{1,36}$"
DocumentId = Annotated[str, StringConstraints(pattern=DOCUMENT_ID_PATTERN)]
DocumentIdArg = Annotated[DocumentId, Field(description="Document id from search results or `list_documents`.")]
ChunkIndex = Annotated[int, Field(ge=0, description="Zero-based chunk index within the document.")]
FilenameFilter = Annotated[
    str | None, Field(max_length=200, description="Case-insensitive substring of the filename, e.g. 'SOP-CMP'.")
]

READ_ONLY = {"read_only_hint": True, "idempotent_hint": True, "open_world_hint": False}


class KnowledgeBaseServer(MCPServer[KnowledgeBaseService]):
    """MCPServer whose resources/list also advertises every ingested document."""

    service: KnowledgeBaseService | None = None

    async def list_resources(self) -> list[MCPResource]:
        resources = await super().list_resources()
        if self.service is None:
            return resources
        try:
            documents = await self.service.searchable_documents()
        except KnowledgeBaseError as exc:
            # Listing must not fail just because the database is down; templates still work.
            logger.warning("resource_listing_degraded", extra={"fields": {"reason": str(exc)}})
            return resources
        return resources + [
            MCPResource(
                uri=document_uri(doc.id),
                name=doc.filename,
                title=doc.filename,
                description=f"{doc.file_type.upper()} document, {doc.total_chunks} chunks.",
                mime_type="text/markdown",
            )
            for doc in documents
        ]


def create_server(
    settings: Settings, *, repository: Repository | None = None, embedder: QueryEmbedder | None = None
) -> KnowledgeBaseServer:
    @asynccontextmanager
    async def lifespan(server: MCPServer) -> AsyncIterator[KnowledgeBaseService]:
        repo = repository or PgVectorRepository(settings)
        service = KnowledgeBaseService(repo, embedder or create_embedder(settings), settings)
        mcp.service = service
        try:
            yield service
        finally:
            mcp.service = None
            if isinstance(repo, PgVectorRepository):
                await repo.close()

    mcp = KnowledgeBaseServer("knowledge-base", version="0.1.0", instructions=INSTRUCTIONS, lifespan=lifespan)

    # ---- Tools --------------------------------------------------------------

    @mcp.tool(annotations=ToolAnnotations(title="Search knowledge base", **READ_ONLY))
    async def search_knowledge_base(
        ctx: Context,
        query: Annotated[
            str, Field(min_length=2, max_length=1000, description="Symptom, alarm, asset id or procedure to look up.")
        ],
        mode: Annotated[
            SearchMode,
            Field(description="'hybrid' (vector + full-text, default), 'semantic' (vector only) or 'keyword'."),
        ] = "hybrid",
        top_k: Annotated[int, Field(ge=1, le=50)] = 5,
        document_ids: Annotated[
            list[DocumentId] | None, Field(max_length=50, description="Only search these documents.")
        ] = None,
        filename_contains: FilenameFilter = None,
        min_similarity: Annotated[
            float | None, Field(ge=-1, le=1, description="Drop vector matches below this cosine similarity.")
        ] = None,
    ) -> SearchResults:
        """Find the document passages most relevant to a query, with filename, page and a resource URI for each."""
        async with tool_call(ctx, "search_knowledge_base") as service:
            return await service.search(
                query,
                mode=mode,
                top_k=top_k,
                document_ids=document_ids,
                filename_contains=filename_contains,
                min_similarity=min_similarity,
            )

    @mcp.tool(annotations=ToolAnnotations(title="List documents", **READ_ONLY))
    async def list_documents(
        ctx: Context,
        status: Annotated[
            DocumentStatus | None, Field(description="Ingestion status filter; 'COMPLETED' = searchable.")
        ] = None,
        filename_contains: FilenameFilter = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> DocumentList:
        """List documents in the knowledge base with their ingestion status and chunk counts."""
        async with tool_call(ctx, "list_documents") as service:
            return await service.list_documents(
                status=status, filename_contains=filename_contains, limit=limit, offset=offset
            )

    @mcp.tool(annotations=ToolAnnotations(title="Read document", **READ_ONLY))
    async def read_document(
        ctx: Context,
        document_id: DocumentIdArg,
        start_chunk: Annotated[int, Field(ge=0, description="First chunk to return.")] = 0,
        max_chunks: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> DocumentPassage:
        """Read a document's text in order, a page of chunks at a time. Continue with `next_chunk` until it is null."""
        async with tool_call(ctx, "read_document") as service:
            return await service.read_document(document_id, start_chunk=start_chunk, max_chunks=max_chunks)

    @mcp.tool(annotations=ToolAnnotations(title="Get chunk context", **READ_ONLY))
    async def get_chunk_context(
        ctx: Context,
        document_id: DocumentIdArg,
        chunk_index: ChunkIndex,
        window: Annotated[int, Field(ge=0, le=10, description="Chunks to include on each side.")] = 1,
    ) -> DocumentPassage:
        """Return a search hit together with its neighbouring chunks, stitched into continuous text."""
        async with tool_call(ctx, "get_chunk_context") as service:
            return await service.get_chunk_context(document_id, chunk_index, window=window)

    @mcp.tool(annotations=ToolAnnotations(title="Knowledge base status", **READ_ONLY))
    async def get_knowledge_base_status(ctx: Context) -> KnowledgeBaseStatus:
        """Report document/chunk counts, ingestion progress and embedding configuration problems."""
        async with tool_call(ctx, "get_knowledge_base_status") as service:
            return await service.status()

    # ---- Resources ----------------------------------------------------------

    @mcp.resource(
        f"{URI_SCHEME}://documents",
        name="documents",
        title="Knowledge base catalog",
        description="All documents with ingestion status, chunk counts and their resource URIs.",
        mime_type="application/json",
    )
    async def document_catalog() -> str:
        # Static resources get no Context, so use the service bound by the lifespan.
        if mcp.service is None:
            raise ResourceError("The knowledge base service is not running.")
        try:
            return await mcp.service.document_catalog_json()
        except KnowledgeBaseError as exc:
            raise ResourceError(str(exc)) from exc

    @mcp.resource(
        f"{URI_SCHEME}://documents/{{document_id}}",
        name="document",
        title="Knowledge base document",
        description="Full text of one ingested document, reassembled from its chunks.",
        mime_type="text/markdown",
    )
    async def document(ctx: Context, document_id: str) -> str:
        async with resource_read(ctx) as service:
            return await service.document_markdown(_checked_id(document_id))

    @mcp.resource(
        f"{URI_SCHEME}://documents/{{document_id}}/chunks/{{chunk_index}}",
        name="document_chunk",
        title="Knowledge base chunk",
        description="One stored chunk of a document, exactly as embedded.",
        mime_type="text/plain",
    )
    async def document_chunk(ctx: Context, document_id: str, chunk_index: str) -> str:
        if not chunk_index.isdigit():
            raise ResourceNotFoundError(f"Invalid chunk index {chunk_index!r}.")
        async with resource_read(ctx) as service:
            return await service.chunk_text(_checked_id(document_id), int(chunk_index))

    return mcp


@asynccontextmanager
async def tool_call(ctx: Context, tool: str) -> AsyncIterator[KnowledgeBaseService]:
    """Per-call plumbing: trace binding, timing/logging and error mapping."""
    bind_trace_id(_incoming_trace_id(ctx))
    started = time.perf_counter()
    outcome = "ok"
    try:
        yield ctx.request_context.lifespan_context
    except KnowledgeBaseError as exc:
        outcome = type(exc).__name__
        raise ToolError(str(exc)) from exc
    finally:
        fields = {"tool": tool, "outcome": outcome, "duration_ms": round((time.perf_counter() - started) * 1000, 1)}
        logger.info("tool_call", extra={"fields": fields})


@asynccontextmanager
async def resource_read(ctx: Context) -> AsyncIterator[KnowledgeBaseService]:
    bind_trace_id(_incoming_trace_id(ctx))
    try:
        yield ctx.request_context.lifespan_context
    except NotFoundError as exc:
        raise ResourceNotFoundError(str(exc)) from exc
    except KnowledgeBaseError as exc:
        raise ResourceError(str(exc)) from exc


def _checked_id(document_id: str) -> str:
    if not re.fullmatch(DOCUMENT_ID_PATTERN, document_id):
        raise ResourceNotFoundError(f"Invalid document id {document_id!r}.")
    return document_id


def _incoming_trace_id(ctx: Context) -> str | None:
    """Reuse the caller's trace id from request `_meta` when present."""
    meta = ctx.request_context.meta or {}
    return meta.get("trace_id")
