"""HTTP flow for tool approval: /chat pauses, /chat/approvals resumes."""

import json
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from apps.backend.app import main
from apps.backend.services.agent_service import AgentService
from scripts.test_agent_service import SERVER_SCRIPT, FakeModel


@pytest.fixture
def client(monkeypatch, tmp_path):
    db = f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}"
    monkeypatch.setattr(main, "create_engine", lambda _settings: create_async_engine(db))
    monkeypatch.setattr(main.settings, "DB_CREATE_TABLES", True)
    monkeypatch.setattr(
        main.settings,
        "MCP_SERVERS_CONFIG",
        json.dumps(
            {
                "servers": [
                    {
                        "name": "sample",
                        "type": "stdio",
                        "params": {"command": sys.executable, "args": [SERVER_SCRIPT]},
                        "require_approval": ["add"],
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(
        main, "AgentService", lambda **_kw: AgentService(model=FakeModel())
    )
    with TestClient(main.app) as c:
        yield c


def _chat(client, session="s1", message="add 2 and 3"):
    return client.post("/chat", json={"session_id": session, "message": message})


def _decide(client, approvals, approved, session="s1", reason=None):
    decisions = [
        {"approval_id": a["approval_id"], "approved": approved, "reason": reason}
        for a in approvals
    ]
    return client.post(
        "/chat/approvals", json={"session_id": session, "decisions": decisions}
    )


def test_chat_pauses_then_approval_completes_turn(client):
    body = _chat(client).json()
    assert body["status"] == "approval_required" and body["reply"] is None
    (approval,) = body["approvals"]
    assert approval["tool_name"] == "add" and approval["server_name"] == "sample"
    assert approval["arguments"] == {"a": 2, "b": 3}

    done = _decide(client, body["approvals"], True)
    assert done.status_code == 200
    assert done.json()["status"] == "completed" and "5" in done.json()["reply"]


def test_rejection_reason_reaches_the_model(client):
    approvals = _chat(client).json()["approvals"]
    done = _decide(client, approvals, False, reason="not now").json()
    assert done["status"] == "completed" and "not now" in done["reply"]


def test_new_message_blocked_while_approval_pending(client):
    _chat(client)
    assert _chat(client, message="hello").status_code == 409
    assert _chat(client, session="other", message="hello").status_code == 200


def test_resolving_without_pending_approval_is_404(client):
    res = client.post(
        "/chat/approvals",
        json={"session_id": "nobody", "decisions": [{"approval_id": "x", "approved": True}]},
    )
    assert res.status_code == 404


def test_bad_decision_keeps_the_pending_run(client):
    approvals = _chat(client).json()["approvals"]
    bad = client.post(
        "/chat/approvals",
        json={"session_id": "s1", "decisions": [{"approval_id": "nope", "approved": True}]},
    )
    assert bad.status_code == 422
    assert _decide(client, approvals, True).json()["status"] == "completed"


def test_approval_cannot_be_replayed(client):
    approvals = _chat(client).json()["approvals"]
    assert _decide(client, approvals, True).status_code == 200
    assert _decide(client, approvals, True).status_code == 404
