# Incident & Ticket Enrichment Copilot

Chat history is managed by the OpenAI Agents SDK `SQLAlchemySession`, persisted in PostgreSQL.

```bash
export DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/db
export OPENAI_API_KEY=...
uv run main.py <session_id> "message"   # same session_id = same conversation
```

Tables `agent_sessions` and `agent_messages` are created on first use.
