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

## Tool approval

`apps/backend/mcp_servers.sample.json` is a ready-made config for the servers in
`mcp_servers/` (Alarm on `:8101`, Ticketing on `:8102`, as started by
`mcp_servers/docker-compose.yml`). Use it with
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
