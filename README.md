# Incident & Ticket Enrichment Copilot

A chat copilot for plant operators at East Refinery. Ask it about active alarms and it will pull the
alarm and asset context, find similar past tickets, retrieve the applicable SOPs and troubleshooting
guides, and draft an incident ticket. **It creates or updates a ticket only after the operator approves.**

The agent is built on the OpenAI Agents SDK. It reaches its data through three
[MCP](https://modelcontextprotocol.io) servers, and it keeps chat history in PostgreSQL.

- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Get started (Docker Compose)](#get-started-docker-compose)
- [Environment file](#environment-file)
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
    UI -->|"SSE: /chat/stream"| Backend["Backend (FastAPI)<br/>agent + chat history<br/>:9200"]
    Backend -->|"MCP"| AlarmMCP["Alarm MCP :9101"]
    Backend -->|"MCP"| TicketMCP["Ticketing MCP :9102"]
    Backend -->|"MCP"| KBMCP["Knowledge Base MCP :9103"]
    AlarmMCP --> Sim["Alarm & Ticketing<br/>simulator :8000"]
    TicketMCP --> Sim
    KBMCP --> PG[("PostgreSQL + pgvector")]
    Backend -->|"chat history"| PG
    Ingest["Ingestion pipeline<br/>(Redis + worker + Gemini)"] -->|"chunks + embeddings"| PG
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

## Repository layout

```
apps/
  backend/                       FastAPI app, agent, MCP loading, prompts/system_prompt.md
  frontend/                      Chat UI (plain HTML/CSS/JS, no build step), served by the backend
  alarms_and_ticket_simulation_api/   Source-system simulator (FastAPI + mock_data.json)
mcp_servers/                     alarm-management, ticketing, knowledge-base MCP servers
ingestion/                       Document ingestion pipeline (upload UI, Redis queue, worker)
test_data/rag_data/              Sample SOPs, troubleshooting guides, KB articles, escalation matrix
tests/                           Backend unit tests
scripts/run_tests.py             Runs every test suite from one command
docs/                            Architecture and chat stream protocol
use_case_docs/                   Assignment brief and Postman collections for the simulator API
docker-compose.yml               Full stack (simulator, 3 MCP servers, backend + UI)
.env.sample                      Environment template for the whole repo
```

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

## Load the knowledge base

The Knowledge Base MCP reads document chunks that the ingestion pipeline wrote to Postgres. A fresh
database has none, so the copilot can answer alarm and ticket questions but has no SOPs to cite until you
ingest some. The sample corpus is in `test_data/rag_data/` (see `DOCUMENT_MANIFEST.md` there).

Ingestion has its own compose file (Redis, a worker and an upload UI) and reads `ingestion/.env`:

```bash
cd ingestion
cp .env.example .env
```

Edit `ingestion/.env`. Point it at the **same database** as the main stack, and set the Gemini key:

```env
GEMINI_API_KEY=your_gemini_api_key
DATABASE_URL=postgresql://USER:PASSWORD@your-endpoint.neon.tech/your_db?sslmode=require
```

Then start it and upload the files:

```bash
docker compose up -d --build
# Open http://localhost:9000, drag in the files from test_data/rag_data/ (.md, .pdf and .docx are accepted)
# and wait until every document shows COMPLETED.
```

The ingestion upload UI is on host port `9000` and its Redis on `6380`, so it can run alongside the main
stack without port clashes. Once the documents show `COMPLETED` you can stop it with `docker compose down`.

Check the result through the Knowledge Base MCP, or just ask the copilot a procedure question. If you
ingested with a real Gemini key, set the **same** `GEMINI_API_KEY`, `GEMINI_EMBEDDING_MODEL` and
`EMBEDDING_DIMENSION` in the root `.env`. If you leave the key empty in both places, mock embeddings are
used on both sides, which is fine for a demo but not for real semantic search.

More detail: [ingestion/README.md](ingestion/README.md).

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

## Further reading

- [docs/architecture.md](docs/architecture.md): components and the end-to-end interaction flow
- [docs/chat-stream-protocol.md](docs/chat-stream-protocol.md): SSE events used by the streaming endpoints
- [apps/backend/prompts/system_prompt.md](apps/backend/prompts/system_prompt.md): the agent's system prompt
- READMEs for each component: [alarm MCP](mcp_servers/alarm-management/README.md),
  [ticketing MCP](mcp_servers/ticketing/README.md), [knowledge-base MCP](mcp_servers/knowledge-base/README.md),
  [ingestion](ingestion/README.md)
- [use_case_docs/](use_case_docs/): the assignment brief, evaluation guidelines and Postman collections for the simulator API
