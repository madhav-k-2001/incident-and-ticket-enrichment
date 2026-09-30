# Ticketing MCP Server

An MCP server that gives AI agents access to the incident Ticketing API (the simulator in the repo root). Agents can look up and search tickets, and create or update them only after an operator approves.

## What was done

- The API's 5 ticket endpoints are exposed as **4 tools**. "Get by id" and "list" are merged into one tool, `find_tickets`.
- **Writes need approval.** `create_ticket` and `update_ticket` only return a preview unless `confirm=true` is passed. The agent is told to show the preview to the operator and ask for approval first.
- Every argument is checked (types, lengths, enums, ticket id pattern) before any API call is made. Unknown fields are rejected, and an update with no changes is rejected.
- API responses are checked against typed models. If the API returns data in an unexpected shape, the tool returns a clear error.
- API errors (not found, bad request, auth, unavailable) become readable tool errors. The API token never appears in errors or logs.
- The HTTP client handles auth, timeouts and retries with backoff. Only GETs are retried, so a create is never sent twice.
- Trace ids: the caller's `trace_id` from the request `_meta` is used, or a new one is generated. It is sent to the API as the `trace-id` header, ends up in the ticket's audit trail, and is returned in every tool result.
- Logs are structured JSON, one line per tool call and per API call.
- Tests: 31, covering the HTTP client, the service logic and in-memory MCP protocol calls.

### Code layout

| File | Responsibility |
|---|---|
| `server.py` | MCP layer: tool definitions, input rules, turning errors into tool errors |
| `service.py` | Logic behind each tool, including the preview/confirm rule for writes |
| `client.py` | HTTP client for the Ticketing API (auth, retries, timeouts, trace header) |
| `models.py` | Pydantic models for tool inputs and outputs |
| `config.py` | Settings from environment variables / `.env` |
| `errors.py` | Error types for API failures |
| `observability.py` | JSON logging and trace ids |

## Tools

| Tool | What it does | API calls used | How to run (example arguments) |
|---|---|---|---|
| `find_tickets` | Get one ticket by id, or list tickets filtered by asset(s), status and severity. Read-only. | `GET /tickets/{id}`, `GET /tickets` | `{"ticket_id": "INC-1042"}` or `{"asset_ids": ["CMP-201", "M-501"], "status": "open"}` |
| `search_similar_tickets` | Rank past tickets by how well they match a symptom or cause. Includes root cause and resolution notes. Read-only. | `GET /tickets/search` | `{"query": "discharge pressure anti-surge valve", "limit": 3}` |
| `create_ticket` | Create an incident ticket. Returns a preview unless `confirm=true`. | `POST /tickets` | Preview: `{"draft": {"title": "Compressor C-201 discharge overpressure", "asset_id": "CMP-201", "severity": "critical", "priority": "P1"}}`. Commit: the same arguments plus `"confirm": true` |
| `update_ticket` | Change status, priority, assignment or notes. The preview shows the current ticket and the pending changes. | `GET /tickets/{id}` (preview), `PATCH /tickets/{id}` (commit) | `{"ticket_id": "INC-1188", "changes": {"status": "resolved", "work_notes": "Bearing replaced"}}`, then the same arguments plus `"confirm": true` |

Typical flow: `search_similar_tickets` → `find_tickets` → `create_ticket` / `update_ticket` (preview → operator approves → confirm).

## How to run

Start the simulator first (from the repo root): `uv run python simulator_app.py`, which serves on `http://localhost:8000`.

| Mode | Command | MCP endpoint |
|---|---|---|
| stdio (local agent / IDE) | `cd mcp-servers/ticketing && TICKETING_API_TOKEN=demo-token uv run ticketing-mcp` | stdio |
| HTTP (local) | `MCP_TRANSPORT=streamable-http TICKETING_API_TOKEN=demo-token uv run ticketing-mcp` | `http://localhost:8100/mcp` |
| Docker (simulator + both MCP servers) | `docker compose -f mcp-servers/docker-compose.yml up --build` (from repo root) | `http://localhost:8102/mcp` |
| Tests | `uv run --group dev pytest -q` | – |

If the alarm MCP server is also running locally over HTTP, it uses port 8100 too. Set `MCP_PORT=8200` (or any free port) for one of them.

### Configuration (environment variables or `.env`, see `.env.example`)

| Variable | Default | Purpose |
|---|---|---|
| `TICKETING_API_BASE_URL` | `http://localhost:8000` | Ticketing API base URL |
| `TICKETING_API_TOKEN` | *(empty)* | Bearer token (`demo-token` for the simulator) |
| `TICKETING_TIMEOUT_SECONDS` | `10` | Timeout per request |
| `TICKETING_MAX_RETRIES` | `2` | Number of retries for GETs on temporary failures |
| `TICKETING_RETRY_BACKOFF_SECONDS` | `0.5` | Delay before the first retry, doubled for each retry after that |
| `TICKETING_CLIENT_ID` | `ticketing-mcp` | Sent as the `x-client-id` header |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8100` | Address the HTTP transport listens on |
| `LOG_LEVEL` | `INFO` | Log level |

### VS Code / Claude Desktop (stdio)

```json
{
  "servers": {
    "ticketing": {
      "command": "uv",
      "args": ["run", "--directory", "mcp-servers/ticketing", "ticketing-mcp"],
      "env": { "TICKETING_API_BASE_URL": "http://localhost:8000", "TICKETING_API_TOKEN": "demo-token" }
    }
  }
}
```
