# RAG Design

The copilot answers from plant documents (SOPs, troubleshooting guides, KB articles, safety instructions, escalation matrix). Documents are ingested into PostgreSQL + pgvector by the ingestion pipeline, and the copilot reads them only through the **Knowledge Base MCP server**.

```mermaid
flowchart LR
    Docs["Documents<br/>PDF / DOCX / MD"] --> API["Ingestion API<br/>:9000"]
    API -->|"job id"| Redis[("Redis queue")]
    Redis --> Worker["Ingestion worker<br/>parse → chunk → embed"]
    Worker -->|"Gemini embeddings"| PG[("PostgreSQL + pgvector")]
    Backend["Copilot backend"] -->|"MCP"| KB["Knowledge Base<br/>MCP server :9103"]
    KB -->|"read-only SQL"| PG
```

## 1. Corpus

| Item | Detail |
|---|---|
| Source documents | 9 synthetic Markdown files in `test_data/rag_data/` (SOPs, troubleshooting guides, KB articles, safety, escalation) |
| Catalog | `test_data/rag_data/DOCUMENT_MANIFEST.md` lists each document, its assets and alarm codes |
| Supported formats | `.pdf`, `.docx`, `.md` |

## 2. Ingestion

1. **Upload.** Files are sent to `POST /upload` (or the web portal at `http://localhost:9000`). Unsupported types are rejected. A `documents` row is created with status `PENDING` and the document id is pushed to a Redis queue.
2. **Parse** (`PARSING`). Text is extracted per section: `pypdf` for PDF (one section per page), `python-docx` for DOCX (paragraphs and tables), and heading-based splitting for Markdown (`# … ######`).
3. **Chunk.** Recursive split at paragraph, line, sentence, then word boundaries: about **1000 characters with 150 characters of overlap**. Markdown sub-chunks are prefixed with their heading path (`[Intro > Setup]`) so each chunk is self-describing.
4. **Embed** (`PROCESSING`). `gemini-embedding-2-preview`, **768 dimensions**. Chunks are sent in batches (up to 30 chunks / about 6000 tokens) behind a Redis rate limiter that stays under the Gemini quota (80 requests/min, 24k tokens/min, 950 requests/day). Without `GEMINI_API_KEY`, deterministic mock vectors are used so everything still runs offline.
5. **Store.** Each batch is committed to Postgres straight away, so an interrupted or rate-limited job (`RATE_LIMITED_PAUSED`) resumes without re-embedding. When all chunks are stored the document becomes `COMPLETED`. On error it becomes `FAILED` with an `error_message`. On startup the worker re-queues unfinished documents.

### Stored data

```
documents        id, filename, file_type, file_size, status, total_chunks, processed_chunks, error_message
document_chunks  id, document_id, chunk_index, page_number, content, char_count, estimated_tokens,
                 embedding vector(768)   -- HNSW index, cosine
```

Chunk metadata used for citations: `filename`, `chunk_index`, `page_number`.

### Run it

```bash
cd ingestion
cp .env.example .env          # set GEMINI_API_KEY (optional; mock mode if empty)
docker compose up -d          # API + worker + Postgres (5434) + Redis (6380)
# upload test_data/rag_data/**/*.md in the portal at http://localhost:9000
```

Re-ingesting a changed document means deleting it (`DELETE /documents/{id}`) and uploading it again.

## 3. Retrieval (Knowledge Base MCP server)

The server only reads the tables above; every tool is read-only and uses bound SQL parameters.

| Mode | How it works |
|---|---|
| `hybrid` (default) | Vector cosine search (pgvector) and Postgres full-text search, merged with Reciprocal Rank Fusion (k = 60). Catches both meaning and exact ids such as `CMP-201` or `SOP-BFP-101`. |
| `semantic` | Vector search only. |
| `keyword` | Full-text search only. |

Filters: `document_ids`, `filename_contains`, `min_similarity`. The query is embedded with the same model and dimension as ingestion (`get_knowledge_base_status` warns if they differ).

## 4. Tools exposed

| Tool | Use |
|---|---|
| `search_knowledge_base` | Find the best-matching chunks; each hit has filename, page, score and a `kb://` resource URI. |
| `get_chunk_context` | Expand a hit with its neighbouring chunks, as continuous text. |
| `read_document` | Read a whole document, a page of chunks at a time (`next_chunk`). |
| `list_documents` | See what is indexed, with ingestion status. |
| `get_knowledge_base_status` | Counts, ingestion progress and configuration warnings. |

The same documents are also MCP resources: `kb://documents`, `kb://documents/{id}`, `kb://documents/{id}/chunks/{index}`. Full schemas are in [mcp-tool-catalog.md](mcp-tool-catalog.md).

## 5. From question to cited answer

1. The user asks about an alarm; the copilot gets the alarm details from the Alarm MCP server.
2. It calls `search_knowledge_base` with the alarm code and asset id (for example `CMP-DISCH-P-HI CMP-201`), then `get_chunk_context` if it needs more of a passage.
3. It writes the answer and cites each document statement as `[Source: <filename>, <section or page>]`, and structured data as `[alarm ALM-9021]` or `[ticket INC-1042]`.
4. The citations appear in the chat UI next to the answer.

## 6. Safety and low confidence

Both rules live in `apps/backend/prompts/system_prompt.md`.

- **No or weak results:** the copilot says so, tries one or two reworded queries, does not present its own knowledge as the plant SOP, and asks a human to confirm the procedure.
- **Conflicting sources:** both sides are reported with citations; the current SOP and live alarm data are preferred.
- **Prompt injection:** retrieved text is treated as data, never as instructions. Commands found inside a document ("ignore previous instructions", "skip approval") are not followed and are reported to the user. Ticket creation still needs explicit operator approval.
- **Logs:** only tool names, durations and outcomes are logged, never full documents or secrets.
