# Incident & Ticket Enrichment Copilot

The Python environment lives in `apps/backend` (`pyproject.toml`, `uv.lock`, `.env`).
Code is imported from the repo root (`apps.backend...`), so run from `apps/backend`:

```bash
cd apps/backend
cp .env.example .env
uv sync
uv run uvicorn apps.backend.app.main:app --app-dir ../.. --reload
uv run pytest
```

## Frontend

`apps/frontend` is the chat UI, **Alarm Investigation and Ticketing**: plain HTML, CSS and
JavaScript modules with no build step. The backend serves it at `/ui/` (and `/` redirects there),
so once the backend is running, open <http://localhost:8000/>.

- Streams replies from `POST /chat/stream` and shows each tool call. Expand a call to see its input and result.
- When a tool needs approval (for example `create_ticket`), the run pauses and shows what the tool
  will do. Approve or reject each call (a rejection can include a reason for the assistant), and the
  same reply continues through `POST /chat/approvals/stream`.
- Messages are limited to 4,000 characters. **Stop** cancels a reply, and **New session** starts a
  new conversation with a new session ID.
- If `API_KEYS` is set, the UI asks for a key the first time the backend returns 401 and keeps it in
  the browser's local storage.
- The current conversation is kept in local storage, so reloading the page does not lose it.

The wire protocol is described in [docs/chat-stream-protocol.md](docs/chat-stream-protocol.md).

## Authentication

Every `/chat*` endpoint (`/chat`, `/chat/stream`, `/chat/approvals`, `/chat/approvals/stream`) requires an API key in the `X-API-Key` header. Set the accepted
key(s) in `apps/backend/.env` as a comma-separated list (list two to rotate):

```bash
API_KEYS=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
curl -X POST localhost:8000/chat -H "X-API-Key: <key>" -H "Content-Type: application/json" \
     -d '{"session_id": "s1", "message": "hello"}'
```

If `API_KEYS` is empty, auth is disabled and a warning is logged on startup.
`/health` is always open so load balancers can probe it.

## Tool approval

`apps/backend/mcp_servers.sample.json` is a ready-made config for the servers in
`mcp_servers/` (Alarm on `:8101`, Ticketing on `:8102`, Knowledge Base on `:8103`, as started by
`docker-compose.yml`). Use it with
`MCP_SERVERS_CONFIG=./mcp_servers.sample.json`, run from `apps/backend`.

In the MCP servers JSON (`MCP_SERVERS_CONFIG`), list the tools the agent must
always get the user's approval for in a server's `require_approval`. Tools not
listed run without asking. It works for every server type, including `hosted`.

```json
{
  "servers": [
    {
      "name": "ticketing",
      "type": "streamable_http",
      "params": { "url": "http://localhost:8002/mcp" },
      "require_approval": ["create_ticket", "close_ticket"]
    }
  ]
}
```

`"always"` / `"never"` (every tool on the server) and the SDK's
`{"always": {"tool_names": [...]}, "never": {"tool_names": [...]}}` are also
accepted. Tool names must match the server's tool names exactly; a name that
matches nothing protects nothing.

When the agent wants to call one of these tools, `POST /chat` pauses and returns
the call instead of a reply:

```json
{
  "session_id": "s1",
  "status": "approval_required",
  "reply": null,
  "approvals": [
    {"approval_id": "call_abc", "tool_name": "create_ticket",
     "server_name": "ticketing", "arguments": {"title": "..."}}
  ]
}
```

Answer every pending approval with `POST /chat/approvals`. A rejection `reason`
is passed to the model, which can then explain or try another route:

```json
{
  "session_id": "s1",
  "decisions": [{"approval_id": "call_abc", "approved": true}]
}
```

The response has the same shape as `/chat`, so it may pause again on a further
tool. While a session is waiting, `/chat` for it returns `409`; decisions that
don't cover exactly the pending approvals get `422` and can be retried; an
unanswered approval expires after `APPROVAL_TTL_SECONDS` (`404` afterwards).

Streaming works the same way: `/chat/stream` ends with an `approval_required`
event instead of `done`, and `POST /chat/approvals/stream` (same body) streams the
rest of the turn. See `docs/chat-stream-protocol.md`.

An approval is used at most once: if the resumed run fails after the tool ran,
it is not offered again and the user sends a new message. Paused runs are kept
in memory, so they are lost on restart and not shared between workers.
