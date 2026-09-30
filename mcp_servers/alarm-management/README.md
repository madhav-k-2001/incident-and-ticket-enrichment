# Alarm Management MCP Server

An MCP server that gives AI agents **read-only** access to the plant Alarm Management API (the simulator in the repo root).

## What was done

- The API's 9 endpoints are grouped into **5 tools**, based on what an agent actually needs to do.
- `get_alarm_context` combines 4 API calls into one. After the alarm is fetched, the asset, priority-score and recommendation calls run at the same time. If one of them fails, the rest of the result is still returned and the failure is listed in `warnings`.
- Every argument is checked (types, lengths, id patterns, enums) before any API call is made.
- API errors become clear tool errors that include a `trace_id`. The API token never appears in errors or logs.
- The HTTP client handles auth, timeouts and retries with backoff on 5xx/429/network errors, and sends the trace id on every request.
- Logs are structured JSON, one line per tool call and per API call.
- Tests: 22, covering the HTTP client, the service logic and in-memory MCP protocol calls.

### Code layout

| File | Responsibility |
|---|---|
| `server.py` | MCP layer: tool definitions, input rules, turning errors into tool errors |
| `service.py` | Logic behind each tool (combining calls, partial results on failure) |
| `client.py` | HTTP client for the Alarm API (auth, retries, timeouts) |
| `models.py` | Pydantic models for tool outputs |
| `config.py` | Settings from environment variables / `.env` |
| `errors.py` | Error types for API failures |
| `observability.py` | JSON logging and trace ids |

## Tools

| Tool | What it does | API calls used | How to run (example arguments) |
|---|---|---|---|
| `search_assets` | Find assets and their metadata, criticality and related assets | `GET /assets/search`, `GET /assets/{id}/metadata` | `{"query": "compressor"}` or `{"asset_id": "CMP-201"}` |
| `list_alarms` | List alarms with filters, sorting and paging | `GET /alarms` | `{"status": "active", "sort_by": "priority_score", "sort_order": "desc"}` |
| `get_alarm_context` | Everything about one alarm: the alarm, its asset, priority (P1–P3) and operator recommendations | `GET /alarms/{id}`, `GET /assets/{id}/metadata`, `POST /alarms/priority-score`, `POST /recommendations/operator-actions` | `{"alarm_id": "ALM-9021"}` |
| `analyze_alarms` | KPIs, severity breakdown, most frequent alarms and a trend over time | `POST /alarms/summary`, `POST /alarms/trends` | `{"asset_ids": ["CMP-201"], "bucket": "daily"}` |
| `find_correlated_alarms` | Cause-and-effect alarm patterns across assets | `POST /alarms/correlation` | `{"asset_ids": ["CMP-201", "M-501"], "lag_window_minutes": 15}` |

Typical flow: `list_alarms` → `get_alarm_context` → `find_correlated_alarms` / `analyze_alarms`.

## How to run

Start the simulator first (from the repo root): `uv run python simulator_app.py`, which serves on `http://localhost:8000`.

| Mode | Command | MCP endpoint |
|---|---|---|
| stdio (local agent / IDE) | `cd mcp-servers/alarm-management && ALARM_API_TOKEN=demo-token uv run alarm-mcp` | stdio |
| HTTP (local) | `MCP_TRANSPORT=streamable-http ALARM_API_TOKEN=demo-token uv run alarm-mcp` | `http://localhost:8100/mcp` |
| Docker (simulator + both MCP servers) | `docker compose up --build` (from repo root) | `http://localhost:8101/mcp` |
| Tests | `uv run --group dev pytest -q` | – |

### Configuration (environment variables or `.env`)

| Variable | Default | Purpose |
|---|---|---|
| `ALARM_API_BASE_URL` | `http://localhost:8000` | Alarm API base URL |
| `ALARM_API_TOKEN` | *(empty)* | Bearer token (`demo-token` for the simulator) |
| `ALARM_API_TIMEOUT_SECONDS` | `10` | Timeout per request |
| `ALARM_API_MAX_RETRIES` | `2` | Number of retries on temporary failures |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8100` | Address the HTTP transport listens on |
| `LOG_LEVEL` | `INFO` | Log level |

### VS Code / Claude Desktop (stdio)

```json
{
  "servers": {
    "alarm-management": {
      "command": "uv",
      "args": ["run", "--directory", "mcp-servers/alarm-management", "alarm-mcp"],
      "env": { "ALARM_API_BASE_URL": "http://localhost:8000", "ALARM_API_TOKEN": "demo-token" }
    }
  }
}
```
