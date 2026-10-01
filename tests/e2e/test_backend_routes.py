"""End-to-end tests for the backend routes, run against a real, running API.

Skipped by default (see ``apps/backend/pyproject.toml``). Run with:

    E2E_BASE_URL=http://localhost:9200 E2E_API_KEY=... pytest -m e2e
    pytest -m "e2e and not llm"      # no OpenAI calls

Tests marked ``llm`` run a real agent turn, so they need OPENAI_API_KEY on the server and
cost tokens. They never approve a tool call: a paused run is always rejected, so no ticket
is created or changed.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.e2e

# Prompt that should not need any tool, so the turn is short and has no side effects.
PING = "Reply with the single word: pong. Do not call any tools."


def _reject_all(client: httpx.Client, session_id: str, approvals: list[dict]) -> None:
    """Leave no side effects: reject every tool call a paused run is waiting on."""
    decisions = [
        {"approval_id": a["approval_id"], "approved": False, "reason": "e2e test"}
        for a in approvals
    ]
    r = client.post("/chat/approvals", json={"session_id": session_id, "decisions": decisions})
    assert r.status_code == 200, r.text


# --- health, docs and UI -------------------------------------------------------------


def test_health_reports_database_and_agent(anonymous):
    r = anonymous.get("/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == {"connected": True}
    assert body["services"]["agent_service"] is True
    assert isinstance(body["version"], str)
    for server in body["mcp_servers"]:
        assert {"name", "type"} <= server.keys()


def test_openapi_lists_the_public_routes(anonymous):
    r = anonymous.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    for path in ("/chat", "/chat/stream", "/chat/approvals", "/chat/approvals/stream", "/health"):
        assert path in paths, f"{path} missing from the OpenAPI schema"


def test_root_redirects_to_ui(anonymous):
    r = anonymous.get("/", follow_redirects=False)
    assert r.status_code in (301, 302, 307, 308)
    assert r.headers["location"].endswith("/ui/")


def test_ui_is_served(anonymous):
    r = anonymous.get("/ui/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


# --- authentication ------------------------------------------------------------------


def test_chat_requires_a_valid_api_key(anonymous):
    # Auth runs before body validation, so an empty body is enough to probe it.
    r = anonymous.post("/chat", json={})
    if r.status_code == 422:
        pytest.skip("API key authentication is disabled on this server (API_KEYS is empty)")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "ApiKey"

    wrong = anonymous.post("/chat", json={}, headers={"X-API-Key": "definitely-not-a-key"})
    assert wrong.status_code == 401


def test_health_stays_open_without_a_key(anonymous):
    assert anonymous.get("/health").status_code == 200


# --- request validation and approval errors (no LLM involved) --------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"session_id": "x"},
        {"message": "hi"},
        {"session_id": "", "message": "hi"},
        {"session_id": "x", "message": ""},
        {"session_id": "x", "message": "   "},
    ],
)
@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
def test_chat_rejects_invalid_requests(client, path, payload):
    assert client.post(path, json=payload).status_code == 422


@pytest.mark.parametrize("path", ["/chat/approvals", "/chat/approvals/stream"])
def test_approvals_reject_invalid_bodies(client, path):
    assert client.post(path, json={}).status_code == 422
    empty = client.post(path, json={"session_id": "x", "decisions": []})
    assert empty.status_code == 422


@pytest.mark.parametrize("path", ["/chat/approvals", "/chat/approvals/stream"])
def test_approvals_for_a_session_with_nothing_pending_are_404(client, session_id, path):
    body = {
        "session_id": session_id,
        "decisions": [{"approval_id": "nope", "approved": False}],
    }
    r = client.post(path, json=body)
    assert r.status_code == 404
    assert "No pending tool approval" in r.json()["detail"]


# --- real agent turns ------------------------------------------------------------------


@pytest.mark.llm
def test_chat_turn_completes_or_pauses_for_approval(client, session_id):
    r = client.post("/chat", json={"session_id": session_id, "message": PING})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == session_id

    if body["status"] == "completed":
        assert body["reply"] and body["reply"].strip()
        assert body["approvals"] == []
    else:
        assert body["status"] == "approval_required"
        assert body["reply"] is None and body["approvals"]
        # A paused session refuses new messages until the approval is answered.
        blocked = client.post("/chat", json={"session_id": session_id, "message": PING})
        assert blocked.status_code == 409
        _reject_all(client, session_id, body["approvals"])


@pytest.mark.llm
def test_chat_stream_emits_ordered_sse_events(client, session_id, parse_sse):
    r = client.post("/chat/stream", json={"session_id": session_id, "message": PING})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(r)
    names = [name for name, _ in events]
    assert names[0] == "run_started"
    assert events[0][1]["session_id"] == session_id
    assert "error" not in names, events
    assert names[-1] in ("done", "approval_required")

    if names[-1] == "done":
        assert events[-1][1]["final_output"]
    else:
        _reject_all(client, session_id, events[-1][1]["approvals"])


@pytest.mark.llm
def test_chat_history_is_kept_per_session(client, session_id):
    first = client.post(
        "/chat",
        json={"session_id": session_id, "message": "Remember the code word PELICAN. Do not call any tools."},
    )
    assert first.status_code == 200, first.text
    if first.json()["status"] != "completed":
        _reject_all(client, session_id, first.json()["approvals"])
        pytest.skip("agent paused for approval instead of answering")

    second = client.post(
        "/chat",
        json={"session_id": session_id, "message": "What was the code word? Do not call any tools."},
    )
    assert second.status_code == 200, second.text
    body = second.json()
    if body["status"] != "completed":
        _reject_all(client, session_id, body["approvals"])
        pytest.skip("agent paused for approval instead of answering")
    assert "pelican" in body["reply"].lower()
