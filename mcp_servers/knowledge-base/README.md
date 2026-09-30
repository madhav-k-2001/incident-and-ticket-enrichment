# Knowledge Base MCP Server

An MCP server that gives AI agents read-only access to the RAG knowledge base built by the ingestion pipeline (`ingestion/`). The knowledge base holds SOPs, troubleshooting guides, KB articles, safety instructions and escalation matrices, stored as embedded chunks in PostgreSQL + pgvector. Agents can search it, read documents, and open every ingested document as an MCP **resource**.

## What was done

- The server reads the tables the ingestion worker writes (see below). It never writes to them, and every tool is annotated read-only.
- **5 tools**: hybrid/semantic/keyword search, document listing, paged document reading, chunk-context expansion, and a status/health check.
- **Resources**: the catalog plus one `kb://documents/{id}` resource per ingested document. They appear in `resources/list`, so clients that browse resources can attach whole documents. Resource templates also cover single chunks.
- **Query embeddings match ingestion**: same Gemini model, output dimension and request shape. Without `GEMINI_API_KEY`, the worker stores deterministic mock vectors, so this server reproduces the same mock. A test pins it to the ingestion function's output.
- **Hybrid search** combines pgvector cosine similarity (HNSW index) with Postgres full-text search using Reciprocal Rank Fusion. Plain vector search misses exact ids such as `CMP-201` or `SOP-BFP-101`; hybrid catches them.
- **Overlap-free text**: the chunker repeats about 150 chars between consecutive chunks. Documents and passages are stitched with that overlap removed. On the sample data, the rebuilt document matches the source file byte for byte.
- Inputs are validated before any query: ids use a strict pattern, and limits and enums are enforced. All SQL uses bound parameters, and `LIKE` wildcards in filename filters are escaped. `file_path` (a server filesystem path) is never returned.
- Database and embedding failures become readable tool errors. The DSN and API key never appear in errors or logs. The connection pool is created lazily, so the server starts even while Postgres is still booting.
- Trace ids come from the request `_meta` (or are generated). Logs are structured JSON, as in the other servers.
- Tests: 42 unit and in-memory MCP protocol tests, plus 7 SQL integration tests against a real pgvector database (opt-in).

### Code layout

| File | Responsibility |
|---|---|
| `server.py` | MCP layer: tools, resources, input rules, turning errors into tool/resource errors |
| `service.py` | Logic behind each tool and resource (hybrid ranking, paging, Markdown rendering) |
| `repository.py` | asyncpg pool and SQL against the pgvector tables |
| `embedder.py` | Query embeddings (Gemini, or the ingestion-compatible mock) |
| `models.py` | Pydantic output models, resource URIs, chunk stitching |
| `config.py` | Settings from environment variables / `.env` |
| `errors.py` | Error types |
| `observability.py` | JSON logging and trace ids |

## Data model (written by `ingestion/worker/worker.py`)

```
documents        id (varchar 36, PK), filename, file_type, file_size, file_path,
                 status (PENDING | PARSING | PROCESSING | RATE_LIMITED_PAUSED | COMPLETED | FAILED),
                 total_chunks, processed_chunks, error_message, created_at, updated_at

document_chunks  id (varchar 36, PK), document_id -> documents.id (ON DELETE CASCADE),
                 chunk_index, page_number, content, char_count, estimated_tokens,
                 embedding vector(768)  -- HNSW index, vector_cosine_ops
                 created_at
```

Chunks are about 1000 characters with a 150-character overlap. Embeddings are `gemini-embedding-2-preview` at 768 dimensions.

## Tools

| Tool | What it does | Example arguments |
|---|---|---|
| `search_knowledge_base` | Rank chunks for a query. `mode`: `hybrid` (default), `semantic` or `keyword`. Optional `document_ids`, `filename_contains` and `min_similarity` filters. Each hit has the filename, page, score and resource URI. | `{"query": "anti-surge valve stuck on CMP-201", "top_k": 5}` |
| `list_documents` | List documents with ingestion status and chunk counts, filtered by status or filename. | `{"status": "COMPLETED", "filename_contains": "SOP"}` |
| `read_document` | Read a document in order, a page of chunks at a time. Continue with `next_chunk`. | `{"document_id": "ae12…", "start_chunk": 0, "max_chunks": 20}` |
| `get_chunk_context` | Return a search hit with its neighbouring chunks, as continuous text. | `{"document_id": "ae12…", "chunk_index": 3, "window": 1}` |
| `get_knowledge_base_status` | Counts by status, total chunks, stored vs configured embedding dimension, and warnings (mock mode, dimension mismatch, pending docs). | `{}` |

Typical flow: `search_knowledge_base` → `get_chunk_context` (or `read_document`) → cite filename and page.

## Resources

| URI | MIME type | Content |
|---|---|---|
| `kb://documents` | `application/json` | Catalog of all documents, including status and resource URIs |
| `kb://documents/{document_id}` | `text/markdown` | Full document text rebuilt from its chunks, with a metadata header. Listed individually for each `COMPLETED` document. |
| `kb://documents/{document_id}/chunks/{chunk_index}` | `text/plain` | One chunk exactly as it was embedded |

## How to run

Start the ingestion stack first, so the pgvector database exists and has documents: `cd ingestion && docker compose up -d`. Postgres is published on `localhost:5434`.

| Mode | Command | MCP endpoint |
|---|---|---|
| stdio (local agent / IDE) | `cd mcp_servers/knowledge-base && uv run knowledge-base-mcp` | stdio |
| HTTP (local) | `MCP_TRANSPORT=streamable-http MCP_PORT=8103 uv run knowledge-base-mcp` | `http://localhost:8103/mcp` |
| Docker (with the other MCP servers) | `docker compose -f mcp_servers/docker-compose.yml up --build` (from the repo root) | `http://localhost:8103/mcp` |
| Tests | `uv run --group dev pytest -q` | – |
| Integration tests | `KB_INTEGRATION=1 uv run --group dev pytest -q`. Needs a Postgres with pgvector at `POSTGRES_*`; it creates and drops a separate `kb_mcp_test` database. | – |

Set `GEMINI_API_KEY` to the same key the ingestion worker uses. If the worker ran without a key (mock embeddings), leave it empty here as well. Otherwise query and document vectors come from different models and semantic ranking is meaningless. `get_knowledge_base_status` warns about this. Query embeddings count against the same Gemini quota as ingestion, but they don't go through the ingestion Redis rate limiter.

### Configuration (environment variables or `.env`, see `.env.example`)

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_HOST` / `POSTGRES_PORT` | `localhost` / `5434` | pgvector database (same names as `ingestion/.env`) |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `postgres` / `postgres` / `ingestion_db` | Credentials and database |
| `DB_POOL_MAX_SIZE` | `5` | Maximum connections in the pool |
| `DB_COMMAND_TIMEOUT_SECONDS` | `10` | Connect and query timeout |
| `GEMINI_API_KEY` | *(empty → mock embeddings)* | Key used to embed queries |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-2-preview` | Must match ingestion |
| `EMBEDDING_DIMENSION` | `768` | Must match the `vector(N)` column |
| `EMBEDDING_TIMEOUT_SECONDS` / `EMBEDDING_MAX_RETRIES` | `15` / `2` | Embedding call limits |
| `MAX_DOCUMENT_CHUNKS` | `2000` | Maximum chunks rendered into one document resource |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8100` | Address the HTTP transport listens on |
| `LOG_LEVEL` | `INFO` | Log level |

### VS Code / Claude Desktop (stdio)

```json
{
  "servers": {
    "knowledge-base": {
      "command": "uv",
      "args": ["run", "--directory", "mcp_servers/knowledge-base", "knowledge-base-mcp"],
      "env": { "POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5434", "GEMINI_API_KEY": "" }
    }
  }
}
```
