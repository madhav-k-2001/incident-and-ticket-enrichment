# Known Limitations

## Testing and CI

- **No CI.** There is no `.github/workflows/ci.yml`; format, lint and tests run locally only (`ruff format`, `python scripts/run_tests.py`).
- **End-to-end tests are limited.** `tests/e2e` calls a running backend over HTTP for the routes and real agent turns. It is excluded from the default run and needs a live stack. It does not yet assert that a single scenario combines MCP tool calls, RAG retrieval and a cited answer, and there are no integration tests.
- **No orchestration tests** for multi-step MCP chains, partial source failure or conflicting evidence. Those behaviours are defined in the system prompt and exercised only manually.
- **No linting or static analysis.** Ruff is used as a formatter only.

## Packaging and operations

- **Postgres is external.** `docker compose up` does not start a database; an external PostgreSQL with pgvector (for example Neon) and an OpenAI key are required.
- **Ingestion runs separately.** It has its own compose file, and documents are uploaded by hand through its portal. There is no automatic index refresh.
- **No health checks or startup ordering** between services in `docker-compose.yml`.

## RAG

- **Citations are filename plus page or chunk.** There is no section-level metadata and no reranker.
- **Prompt-injection defence is prompt-based.** Retrieved text is treated as data by instruction; there is no content filtering.
- **Semantic search needs a Gemini key.** Without it, mock embeddings are used and ranking is meaningless.

## Security and observability

- **Auth is one shared API key**, with no per-user roles.
- **Write approval lives in the backend.** The ticketing MCP server writes immediately if called directly.
- **Backend logging is basic.** The MCP servers log structured JSON with trace ids. The backend does not yet log request or conversation ids, LLM latency or retrieval scores.

## Data

- **The Alarm/Ticketing API is a simulator.** Data is synthetic, and the document corpus is 9 synthetic samples.
