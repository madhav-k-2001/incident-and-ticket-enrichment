# MCP Tool Catalog

Three MCP servers (streamable HTTP, `/mcp`) expose 14 tools. The copilot calls them through its MCP client; it never calls the source systems directly.

| Server | Port | Source system | Tools |
|---|---|---|---|
| `alarm-management` | 9101 | Alarm Management API | `search_assets`, `list_alarms`, `get_alarm_context`, `analyze_alarms`, `find_correlated_alarms` |
| `ticketing` | 9102 | Ticketing API | `find_tickets`, `search_similar_tickets`, `create_ticket`, `update_ticket` |
| `knowledge-base` | 9103 | PostgreSQL + pgvector (RAG) | `search_knowledge_base`, `list_documents`, `read_document`, `get_chunk_context`, `get_knowledge_base_status` |

Start a server on its own: see each server's README in `mcp_servers/<name>/`.

## Behaviour common to all tools

| Topic | Behaviour |
|---|---|
| Discovery | `tools/list` returns every tool with its JSON input schema. |
| Input validation | Types, lengths, enums and id patterns are checked before any call. Invalid input is rejected with a tool error. |
| Output | Every result is a typed model and includes `trace_id`. |
| Trace | Send `trace_id` in the request `_meta` (alarm server also accepts the `x-trace-id` header). If absent, one is generated. It is forwarded to the source API and logged. |
| Errors | Source failures become a tool error (`isError`) with a readable message. Secrets and URLs are never included. |
| Logging | One JSON log line per tool call: `tool`, `outcome`, `duration_ms`. |

Error mapping (alarm and ticketing): `404` → not found, `400/422` → invalid request, `401/403` → authentication failed, anything else → unavailable.

## Alarm Management server

**Auth:** `Authorization: Bearer ${ALARM_API_TOKEN}` on every API request. **Timeout:** `ALARM_API_TIMEOUT_SECONDS` (default 10 s) per request. **Retries:** 2 retries with exponential backoff on 429/502/503/504 and network errors. All tools are read-only.

### `search_assets`

- **Purpose:** Find assets, or turn an asset name into an `asset_id`.
- **Input:** `query?` (string ≤100), `asset_id?` (exact id; other filters ignored), `site?`, `unit?`, `limit` (1–50, default 10).
- **Output:** `{ assets: [{asset_id, asset_name, site, unit, type, criticality, related_assets}], total, trace_id }`
- **Source operation:** `GET /assets/search`, `GET /assets/{id}/metadata`
- **Errors / timeout:** unknown `asset_id` → not found; otherwise the common behaviour above.
- **Example call:** `{"query": "compressor"}`
- **Example response:** `{"assets": [{"asset_id": "CMP-201", "asset_name": "Compressor C-201", "criticality": "high", ...}], "total": 1, "trace_id": "9f2c..."}`

### `list_alarms`

- **Purpose:** List alarms with filters, sorting and paging.
- **Input:** `asset_id?`, `site?`, `unit?`, `status?` (`active|acknowledged|cleared`), `severity?` (`critical|high|medium|low`), `start_time?`, `end_time?`, `sort_by` (`start_time|priority_score|severity`), `sort_order` (`asc|desc`), `page` (≥1), `page_size` (1–100, default 20).
- **Output:** `{ alarms: [Alarm], pagination: {page, page_size, total_count, total_pages}, trace_id }`
- **Source operation:** `GET /alarms`
- **Errors / timeout:** common behaviour. Invalid filter values are rejected before the call.
- **Example call:** `{"status": "active", "sort_by": "priority_score", "sort_order": "desc", "page_size": 5}`
- **Example response:** `{"alarms": [{"alarm_id": "ALM-9021", "asset_id": "CMP-201", "severity": "critical", "status": "active", "priority_score": 92.5, ...}], "pagination": {"page": 1, "page_size": 5, "total_count": 14, "total_pages": 3}, "trace_id": "9f2c..."}`

### `get_alarm_context`

- **Purpose:** Everything needed to act on one alarm in a single call.
- **Input:** `alarm_id` (required, `[A-Za-z0-9_.-]`, ≤64).
- **Output:** `{ alarm, asset, priority, recommendations, warnings, trace_id }`. `asset`, `priority` and `recommendations` are `null` if that lookup failed; the reason is listed in `warnings` (partial result).
- **Source operation:** `GET /alarms/{id}`, then in parallel `GET /assets/{id}/metadata`, `POST /alarms/priority-score`, `POST /recommendations/operator-actions`
- **Errors / timeout:** alarm not found → tool error. Failure of an enrichment call does not fail the tool.
- **Example call:** `{"alarm_id": "ALM-9021"}`
- **Example response:** `{"alarm": {...}, "asset": {...}, "priority": {"priority_score": 92.5, "urgency": "immediate", "recommended_priority_level": "P1"}, "recommendations": {"likely_causes": [...], "immediate_actions": [...], "safety_precautions": [...]}, "warnings": [], "trace_id": "9f2c..."}`

### `analyze_alarms`

- **Purpose:** Alarm KPIs, severity breakdown, most frequent alarms and trend.
- **Input:** `asset_ids?` (≤50), `severities?`, `start_time?`, `end_time?`, `bucket` (`hourly|daily|weekly`, default `daily`).
- **Output:** `{ total_alarms, severity_breakdown, kpis, top_alarms, trend_bucket, trend, warnings, trace_id }`. If the trend call fails, `trend` is empty and `warnings` says why.
- **Source operation:** `POST /alarms/summary`, `POST /alarms/trends`
- **Errors / timeout:** common behaviour; partial result on trend failure.
- **Example call:** `{"asset_ids": ["CMP-201"], "bucket": "daily"}`
- **Example response:** `{"total_alarms": 18, "severity_breakdown": {"critical": 3, "high": 7}, "kpis": {"recurrence_rate": 0.4}, "top_alarms": [{"group_key": "HIGH_DISCHARGE_PRESSURE", "alarm_count": 6}], "trend": [...], "warnings": [], "trace_id": "9f2c..."}`

### `find_correlated_alarms`

- **Purpose:** Find alarms that tend to follow one another across assets.
- **Input:** `asset_ids?`, `start_time?`, `end_time?`, `lag_window_minutes` (1–1440, default 15), `severity_threshold` (default `medium`), `min_support` (≥1, default 1).
- **Output:** `{ correlations: [{source_asset, target_asset, source_alarm_code, target_alarm_code, correlation_coefficient, avg_lag_minutes, ...}], trace_id }`
- **Source operation:** `POST /alarms/correlation`
- **Errors / timeout:** common behaviour.
- **Example call:** `{"asset_ids": ["CMP-201", "M-501"], "lag_window_minutes": 15}`
- **Example response:** `{"correlations": [{"source_asset": "CMP-201", "target_asset": "M-501", "correlation_coefficient": 0.82, "avg_lag_minutes": 6.5}], "trace_id": "9f2c..."}`

## Ticketing server

**Auth:** `Authorization: Bearer ${TICKETING_API_TOKEN}` on every API request. **Timeout:** `TICKETING_TIMEOUT_SECONDS` (default 10 s). **Retries:** 2 retries with backoff, **GET requests only**, so a create is never sent twice. **Write approval:** `create_ticket` and `update_ticket` write immediately; the backend requires operator approval for them (`require_approval` in `apps/backend/mcp_servers.json`).

### `find_tickets`

- **Purpose:** Fetch a ticket by id, or list tickets by asset, status and severity.
- **Input:** `ticket_id?` (`[A-Za-z0-9_-]`, ≤32; other filters ignored), `asset_ids?`, `status?` (`open|in_progress|resolved|closed`), `severity?`, `limit` (1–100, default 20).
- **Output:** `{ tickets: [Ticket], total, trace_id }`
- **Source operation:** `GET /tickets/{id}` or `GET /tickets`
- **Errors / timeout:** unknown id → not found; otherwise common behaviour.
- **Example call:** `{"asset_ids": ["CMP-201"], "status": "open"}`
- **Example response:** `{"tickets": [{"ticket_id": "INC-1042", "asset_id": "CMP-201", "title": "...", "status": "open", "priority": "P2"}], "total": 1, "trace_id": "9f2c..."}`

### `search_similar_tickets`

- **Purpose:** Rank historical tickets by relevance to a symptom or cause, with root cause and resolution notes.
- **Input:** `query` (3–500 chars), `asset_id?`, `limit` (1–20, default 5).
- **Output:** `{ tickets: [Ticket with relevance_score], total, trace_id }`
- **Source operation:** `GET /tickets/search`
- **Errors / timeout:** common behaviour.
- **Example call:** `{"query": "discharge pressure anti-surge valve", "limit": 3}`
- **Example response:** `{"tickets": [{"ticket_id": "INC-0988", "root_cause": "...", "resolution_notes": "...", "relevance_score": 8}], "total": 3, "trace_id": "9f2c..."}`

### `create_ticket`  (write)

- **Purpose:** Create an incident ticket.
- **Input:** `draft`: `title` (5–200), `asset_id` (required), `alarm_id?`, `severity` (default `high`), `priority` (`P1–P4`, default `P2`), `symptom`, `likely_cause`, `recommended_action`, `sop_reference`, `assigned_to`, `assigned_user`, `similar_ticket_ref?`. Unknown fields are rejected.
- **Output:** `{ message, ticket, trace_id }`
- **Source operation:** `POST /tickets`
- **Errors / timeout:** common behaviour; **never retried**.
- **Example call:** `{"draft": {"title": "Compressor C-201 discharge overpressure", "asset_id": "CMP-201", "severity": "critical", "priority": "P1"}}`
- **Example response:** `{"message": "Ticket created", "ticket": {"ticket_id": "INC-1201", "status": "open", ...}, "trace_id": "9f2c..."}`

### `update_ticket`  (write)

- **Purpose:** Change status, priority, assignment or notes on a ticket.
- **Input:** `ticket_id`, `changes`: any of `status`, `priority`, `resolution_notes`, `assigned_to`, `assigned_user`, `work_notes`. At least one is required.
- **Output:** `{ message, ticket, trace_id }`
- **Source operation:** `PATCH /tickets/{id}`
- **Errors / timeout:** unknown id → not found; empty `changes` → validation error; **never retried**.
- **Example call:** `{"ticket_id": "INC-1188", "changes": {"status": "resolved", "work_notes": "Bearing replaced"}}`
- **Example response:** `{"message": "Ticket updated", "ticket": {"ticket_id": "INC-1188", "status": "resolved", ...}, "trace_id": "9f2c..."}`

## Knowledge Base server

**Auth:** no per-request auth; the server connects to Postgres with `POSTGRES_*` credentials and embeds queries with `GEMINI_API_KEY` (mock embeddings if unset). **Timeout:** `DB_COMMAND_TIMEOUT_SECONDS` (default 10 s) for connect and query; `EMBEDDING_TIMEOUT_SECONDS` (default 15 s) per embedding call, with `EMBEDDING_MAX_RETRIES` (default 2) retries. All tools are read-only and use bound SQL parameters. Database or embedding outage → "unavailable" tool error; missing document or chunk → not found.

### `search_knowledge_base`

- **Purpose:** Find the most relevant document passages for a query (used for RAG citations).
- **Input:** `query` (2–1000 chars), `mode` (`hybrid|semantic|keyword`, default `hybrid`), `top_k` (1–50, default 5), `document_ids?`, `filename_contains?`, `min_similarity?` (−1 to 1).
- **Output:** `{ query, mode, embedding_mode, hits: [{chunk_id, document_id, filename, chunk_index, page_number, content, score, similarity, keyword_rank, resource_uri}], trace_id }`
- **Source operation:** SQL on `document_chunks` (pgvector cosine + full-text, fused with RRF).
- **Errors / timeout:** common behaviour; no matches returns empty `hits`.
- **Example call:** `{"query": "anti-surge valve stuck on CMP-201", "top_k": 3}`
- **Example response:** `{"hits": [{"filename": "TSG-CMP-AntiSurge-Valve-Diagnostics.md", "page_number": 1, "chunk_index": 2, "score": 0.031, "resource_uri": "kb://documents/ae12.../chunks/2", "content": "..."}], "embedding_mode": "gemini", "trace_id": "9f2c..."}`

### `list_documents`

- **Purpose:** List documents with ingestion status and chunk counts.
- **Input:** `status?` (`PENDING|PARSING|PROCESSING|RATE_LIMITED_PAUSED|COMPLETED|FAILED`), `filename_contains?`, `limit` (1–200, default 50), `offset` (≥0).
- **Output:** `{ documents: [{id, filename, file_type, status, total_chunks, resource_uri, ...}], total, trace_id }`
- **Source operation:** SQL on `documents`.
- **Errors / timeout:** common behaviour.
- **Example call:** `{"status": "COMPLETED", "filename_contains": "SOP"}`
- **Example response:** `{"documents": [{"id": "ae12...", "filename": "SOP-CMP-201-Discharge-Overpressure.md", "status": "COMPLETED", "total_chunks": 7}], "total": 3, "trace_id": "9f2c..."}`

### `read_document`

- **Purpose:** Read a document in order, a page of chunks at a time.
- **Input:** `document_id`, `start_chunk` (≥0, default 0), `max_chunks` (1–100, default 20).
- **Output:** `{ document, first_chunk, last_chunk, text, chunks, next_chunk, trace_id }`. Pass `next_chunk` as `start_chunk` to continue; `null` means the end.
- **Source operation:** SQL on `documents` and `document_chunks`.
- **Errors / timeout:** unknown document → not found; `start_chunk` past the end → invalid request.
- **Example call:** `{"document_id": "ae12...", "start_chunk": 0, "max_chunks": 20}`
- **Example response:** `{"first_chunk": 0, "last_chunk": 6, "text": "# SOP-CMP-201 ...", "next_chunk": null, "trace_id": "9f2c..."}`

### `get_chunk_context`

- **Purpose:** Return a search hit together with its neighbouring chunks as continuous text.
- **Input:** `document_id`, `chunk_index` (≥0), `window` (0–10, default 1).
- **Output:** same shape as `read_document`.
- **Source operation:** SQL on `document_chunks`.
- **Errors / timeout:** unknown document or chunk → not found.
- **Example call:** `{"document_id": "ae12...", "chunk_index": 3, "window": 1}`
- **Example response:** `{"first_chunk": 2, "last_chunk": 4, "text": "...", "next_chunk": 5, "trace_id": "9f2c..."}`

### `get_knowledge_base_status`

- **Purpose:** Health check: counts, ingestion progress and configuration warnings (mock mode, dimension mismatch).
- **Input:** none.
- **Output:** `{ documents_by_status, total_documents, total_chunks, stored_embedding_dimension, configured_embedding_dimension, embedding_model, embedding_mode, warnings, trace_id }`
- **Source operation:** SQL counts on `documents` and `document_chunks`.
- **Errors / timeout:** database unreachable → unavailable.
- **Example call:** `{}`
- **Example response:** `{"documents_by_status": {"COMPLETED": 9}, "total_documents": 9, "total_chunks": 64, "embedding_mode": "gemini", "warnings": [], "trace_id": "9f2c..."}`

The knowledge-base server also exposes the documents as MCP resources: `kb://documents`, `kb://documents/{id}` and `kb://documents/{id}/chunks/{index}`.
