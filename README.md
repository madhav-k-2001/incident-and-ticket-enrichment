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
