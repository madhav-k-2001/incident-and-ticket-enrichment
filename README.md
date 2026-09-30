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

## Authentication

`POST /chat` requires an API key in the `X-API-Key` header. Set the accepted
key(s) in `apps/backend/.env` as a comma-separated list (list two to rotate):

```bash
API_KEYS=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
curl -X POST localhost:8000/chat -H "X-API-Key: <key>" -H "Content-Type: application/json" \
     -d '{"session_id": "s1", "message": "hello"}'
```

If `API_KEYS` is empty, auth is disabled and a warning is logged on startup.
`/health` is always open so load balancers can probe it.
