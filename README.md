# Incident & Ticket Enrichment Copilot

A chat copilot for plant operators at East Refinery. Ask it about active alarms and it will pull the
alarm and asset context, find similar past tickets, retrieve the applicable SOPs and troubleshooting
guides, and draft an incident ticket. **It creates or updates a ticket only after the operator approves.**

The agent is built on the OpenAI Agents SDK. It reaches its data through three
[MCP](https://modelcontextprotocol.io) servers, and it keeps chat history in PostgreSQL.

- [Architecture](#architecture)
- [Get started (Docker Compose)](#get-started-docker-compose)
- [Environment file](#environment-file)
- [Local dev setup](#local-dev-setup)
- [Load the knowledge base](#load-the-knowledge-base)
- [Using the app](#using-the-app)
- [Run without Docker](#run-without-docker)
- [Backend API](#backend-api)
- [Authentication](#authentication)
- [Tool approval](#tool-approval)
- [Tests](#tests)
- [Troubleshooting](#troubleshooting)
- [Further reading](#further-reading)

## Architecture

```mermaid
flowchart LR
    User["Operator"] --> UI["Chat UI<br/>/ui/"]
    UI -->|"HTTP (SSE): /chat/stream"| Backend["Backend (FastAPI)<br/>agent + chat history<br/>:9200"]
    Backend -->|"HTTP (MCP)"| AlarmMCP["Alarm MCP :9101"]
    Backend -->|"HTTP (MCP)"| TicketMCP["Ticketing MCP :9102"]
    Backend -->|"HTTP (MCP)"| KBMCP["Knowledge Base MCP :9103"]
    AlarmMCP -->|"HTTP (REST)"| Sim["Alarm & Ticketing<br/>simulator :8000"]
    TicketMCP -->|"HTTP (REST)"| Sim
    KBMCP -->|"SQL"| PG[("PostgreSQL + pgvector")]
    Backend -->|"SQL: chat history"| PG
    Ingest["Ingestion pipeline<br/>(Redis + worker + Gemini)"] -->|"SQL: chunks + embeddings"| PG
```

| Service | Folder | Host port | What it does |
|---|---|---|---|
| Backend API + chat UI | `apps/backend`, `apps/frontend` | 9200 | Runs the agent, streams replies, pauses for tool approval, stores chat history |
| Alarm & ticketing simulator | `apps/alarms_and_ticket_simulation_api` | 8000 | Mock source system, serves `mock_data.json`, accepts Bearer `demo-token` |
| Alarm MCP | `mcp_servers/alarm-management` | 9101 | Tools: `search_assets`, `list_alarms`, `get_alarm_context`, `analyze_alarms`, `find_correlated_alarms` |
| Ticketing MCP | `mcp_servers/ticketing` | 9102 | Tools: `find_tickets`, `search_similar_tickets`, `create_ticket`, `update_ticket` |
| Knowledge Base MCP | `mcp_servers/knowledge-base` | 9103 | Read-only RAG tools over the ingested documents: `search_knowledge_base`, `list_documents`, `read_document`, `get_chunk_context`, `get_knowledge_base_status` |
| Ingestion pipeline | `ingestion` | own compose file | Parses documents, chunks them, embeds them with Gemini, writes them to Postgres |

The compose stack does **not** start a database. All services share one external PostgreSQL database
(the project uses [Neon](https://neon.tech)) that has the `pgvector` extension available.

## Get started (Docker Compose)

### Prerequisites

- Docker with Compose v2 (`docker compose version`)
- An **OpenAI API key** (the agent's LLM)
- A **PostgreSQL database with pgvector** that the containers can reach, for example a free Neon project
- A **Gemini API key** is optional for a first run (see [Load the knowledge base](#load-the-knowledge-base))

### 1. Create your `.env`

```bash
cp .env.sample .env
```

On Windows PowerShell use `Copy-Item .env.sample .env`.

Then fill in at least the OpenAI key and the database settings. The smallest working file is shown in
[Environment file](#environment-file).

### 2. Build and start the stack

```bash
docker compose up --build
```

Add `-d` to run in the background. The first build downloads base images and dependencies, so it takes a
few minutes.

### 3. Check that it is up

```bash
curl http://localhost:9200/health
```

A healthy response looks like this. `database.connected` must be `true`, and `mcp_servers` should list
`alarm_management`, `ticketing` and `knowledge_base`:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "database": {"connected": true},
  "services": {"agent_service": true},
  "mcp_servers": [
    {"name": "alarm_management", "type": "MCPServerStreamableHttp"},
    {"name": "ticketing", "type": "MCPServerStreamableHttp"},
    {"name": "knowledge_base", "type": "MCPServerStreamableHttp"}
  ]
}
```

### 4. Open the app

| URL | What |
|---|---|
| <http://localhost:9200/> | Chat UI (redirects to `/ui/`) |
| <http://localhost:9200/docs> | Backend OpenAPI docs |
| <http://localhost:8000/docs> | Simulator OpenAPI docs |
| <http://localhost:9101/mcp>, `:9102/mcp`, `:9103/mcp` | MCP endpoints (streamable HTTP) |

### Stop and clean up

```bash
docker compose down            # stop and remove containers
docker compose logs -f backend # follow one service's logs
docker compose up --build backend   # rebuild and restart a single service
```

## Environment file

`.env.sample` at the repo root is the full template, with every setting used by the backend, the
simulator, the three MCP servers and the ingestion pipeline. `docker-compose.yml` reads `.env` for variable
substitution, and the backend container loads the whole file as its environment.

This is the **minimum** you need to run the compose stack:

```env
# --- LLM (required) ---------------------------------------------------------
OPENAI_API_KEY=sk-your-openai-key
# AGENT_MODEL=                 # optional, leave unset to use the SDK default

# --- Database (required): one PostgreSQL + pgvector shared by all services --
# Backend (chat history). Wins over the POSTGRES_* parts below.
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@your-endpoint.neon.tech/your_db?ssl=require

# Knowledge Base MCP (and ingestion). Same database, as separate parts.
POSTGRES_USER=USER
POSTGRES_PASSWORD=PASSWORD
POSTGRES_HOST=your-endpoint.neon.tech
POSTGRES_PORT=5432
POSTGRES_DB=your_db
POSTGRES_SSLMODE=require

# Create the chat history tables (agent_sessions, agent_messages) on startup.
# Set to false once the tables exist / in production.
DB_CREATE_TABLES=true

# --- Backend auth -----------------------------------------------------------
# Comma-separated keys accepted in the X-API-Key header. Empty = auth disabled (dev only).
# Generate: python -c "import secrets; print(secrets.token_urlsafe(32))"
API_KEYS=

# --- Embeddings (optional) --------------------------------------------------
# Leave empty to use deterministic mock embeddings. Set it to the same key and model
# you used for ingestion, otherwise search results will be meaningless.
GEMINI_API_KEY=
GEMINI_EMBEDDING_MODEL=gemini-embedding-2-preview
EMBEDDING_DIMENSION=768
```

Everything else has a default in `docker-compose.yml` or in the backend settings, and you only set it to
override. The settings you are most likely to touch:

| Variable | Default | Used by | Notes |
|---|---|---|---|
| `OPENAI_API_KEY` | none | backend | **Required.** Read by the OpenAI Agents SDK |
| `AGENT_MODEL` | SDK default | backend | Model name for the agent |
| `AGENT_MAX_TURNS` | `10` | backend | Max agent loop turns per request |
| `API_KEYS` | empty (auth off) | backend | Comma-separated, list two to rotate keys |
| `DATABASE_URL` | none | backend | Wins over `POSTGRES_*`. `postgres://`, `postgresql://` and `sslmode=` are converted for asyncpg |
| `POSTGRES_USER` / `_PASSWORD` / `_HOST` / `_PORT` / `_DB` | | backend, KB MCP, ingestion | `_HOST` and `_USER` have no default in compose and must be set |
| `POSTGRES_SSLMODE` | none | backend, ingestion | Use `require` for Neon |
| `DB_CREATE_TABLES` | `false` in code | backend | `true` creates the chat history tables on startup |
| `MCP_SERVERS_CONFIG` | | backend | Compose overrides this with `apps/backend/mcp_servers.docker.json` (container network names). The value in `.env` is only used when you run the backend outside Docker |
| `APPROVAL_TTL_SECONDS` | `900` | backend | How long a paused run waits for the user's decision |
| `SSE_HEARTBEAT_SECONDS` | `15` | backend | Keep-alive interval on streaming responses |
| `ALARM_API_TOKEN`, `TICKETING_API_TOKEN` | `demo-token` | MCP servers | The simulator accepts `demo-token` |
| `ALARM_API_*`, `TICKETING_*` timeouts and retries | see `.env.sample` | MCP servers | |
| `GEMINI_API_KEY` | empty (mock embeddings) | KB MCP, ingestion | Must match between the two |
| `LOG_LEVEL` | `INFO` | backend, MCP servers | |

Notes:

- **The database must be reachable from inside the containers.** `localhost` in `.env` means the container
  itself. For a Postgres on your own machine use `host.docker.internal` as the host. A managed database
  such as Neon works as is.
- **pgvector:** the ingestion pipeline runs `CREATE EXTENSION IF NOT EXISTS vector`, so the database user
  needs permission to do that, or the extension must already be enabled.
- **`.env` is git-ignored and excluded from Docker build contexts.** Never commit it.
- Per-component templates are also available: `apps/backend/.env.example`, `ingestion/.env.example` and
  `mcp_servers/*/.env.example`.

## Local dev setup

[`scripts/dev-setup.sh`](scripts/dev-setup.sh) prepares a local checkout in one step. It needs [uv](https://docs.astral.sh/uv/):

```bash
./scripts/dev-setup.sh
```

It first checks that git, uv and Docker with Compose v2 are installed (it warns if Python 3.12+ is not on your PATH) and stops with a list of anything missing. It then creates `.env` from `.env.sample` (if missing), a root `.venv` with the backend, simulator and ingestion
dependencies, and a `.venv` in each MCP server. Then `python scripts/run_tests.py` runs every test suite.

## Load the knowledge base

A fresh database has no documents, so the copilot has no SOPs to cite until you ingest some. Use the
ingestion pipeline (its own compose file, reads `ingestion/.env`) with the sample files in `test_data/rag_data/`:

```bash
cd ingestion
cp .env.example .env     # set GEMINI_API_KEY and the same database as the main stack
docker compose up -d --build
```

Open <http://localhost:9000>, upload the files and wait until each shows `COMPLETED`. Use the same
`GEMINI_API_KEY`, `GEMINI_EMBEDDING_MODEL` and `EMBEDDING_DIMENSION` in the root `.env`. Details:
[ingestion/README.md](ingestion/README.md).

## Tool approval

`apps/backend/mcp_servers.docker.json` (used by compose) and `apps/backend/mcp_servers.sample.json` (for
running outside Docker) describe the three MCP servers. In that JSON, list the tools the agent must always
get the user's approval for in a server's `require_approval`. Tools not listed run without asking. It works
for every server type, including `hosted`. The shipped config protects the two write tools:

```json
{
  "servers": [
    {
      "name": "ticketing",
      "type": "streamable_http",
      "params": { "url": "http://ticketing-mcp:8100/mcp" },
      "require_approval": ["create_ticket", "update_ticket"]
    }
  ]
}
```

`"always"` / `"never"` (every tool on the server) and the SDK's
`{"always": {"tool_names": [...]}, "never": {"tool_names": [...]}}` are also accepted. Tool names must match
the server's tool names exactly; a name that matches nothing protects nothing.

## Tests

Every suite (backend, simulator, the three MCP servers, ingestion) runs from one command at the repo root:

```bash
python scripts/run_tests.py              # all suites
python scripts/run_tests.py --list       # list suites
python scripts/run_tests.py backend -j 4 # pick suites / run in parallel
python scripts/run_tests.py --skip ingestion   # ingestion's end-to-end test takes ~100s
python scripts/run_tests.py backend -- -k approval   # extra args go to pytest
```

Each suite uses its own `.venv` if it has one, otherwise the repo-root `.venv`. The exit code is non-zero if
any suite fails. To run just the backend tests directly:

```bash
cd apps/backend && uv run pytest
```

### End-to-end tests (real API)

`tests/e2e` holds end-to-end tests for the backend routes. They call a **running** backend over HTTP (no
mocks, nothing imported from the app), so they are marked `e2e` and **excluded from the default run**:
`pytest`, `python scripts/run_tests.py` and CI only run the unit tests. They skip themselves if
`E2E_BASE_URL` is not set.

```bash
docker compose up -d                       # or any running backend
cd apps/backend
E2E_BASE_URL=http://localhost:9200 E2E_API_KEY=<one of API_KEYS> uv run pytest -m e2e
uv run pytest -m "e2e and not llm"         # skip the tests that run a real agent turn
```

| Variable | Default | Meaning |
|---|---|---|
| `E2E_BASE_URL` | unset (tests skip) | Base URL of the running backend |
| `E2E_API_KEY` | none | `X-API-Key` to send, needed when the server sets `API_KEYS` |
| `E2E_TIMEOUT_SECONDS` | `120` | Per-request timeout |

Tests marked `llm` run real agent turns, so they need `OPENAI_API_KEY` on the server and cost tokens.
They never approve a tool call (a paused run is rejected), so no ticket is created or changed. Chat
sessions they create stay in the database under ids starting with `e2e-`.

## Further reading

- [docs/architecture.md](docs/architecture.md): components and the end-to-end interaction flow
- [docs/chat-stream-protocol.md](docs/chat-stream-protocol.md): SSE events used by the streaming endpoints
- [apps/backend/prompts/system_prompt.md](apps/backend/prompts/system_prompt.md): the agent's system prompt
- READMEs for each component: [alarm MCP](mcp_servers/alarm-management/README.md),
  [ticketing MCP](mcp_servers/ticketing/README.md), [knowledge-base MCP](mcp_servers/knowledge-base/README.md),
  [ingestion](ingestion/README.md)
- [use_case_docs/](use_case_docs/): the assignment brief, evaluation guidelines and Postman collections for the simulator API
