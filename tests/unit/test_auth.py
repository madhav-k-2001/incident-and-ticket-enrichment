import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from apps.backend.app import auth
from apps.backend.app.config import Settings


def make_client(monkeypatch, api_keys: str) -> TestClient:
    monkeypatch.setattr(auth, "get_settings", lambda: Settings(API_KEYS=api_keys, _env_file=None))
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(auth.require_api_key)])
    def protected():
        return {"ok": True}

    return TestClient(app)


def test_api_keys_parsing():
    assert Settings(API_KEYS=" a, b ,,c", _env_file=None).api_keys == ["a", "b", "c"]
    assert Settings(API_KEYS="", _env_file=None).api_keys == []


def test_missing_key_rejected(monkeypatch):
    r = make_client(monkeypatch, "secret").get("/protected")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "ApiKey"


def test_wrong_key_rejected(monkeypatch):
    r = make_client(monkeypatch, "secret").get("/protected", headers={"X-API-Key": "nope"})
    assert r.status_code == 401


@pytest.mark.parametrize("key", ["old", "new"])
def test_any_configured_key_accepted(monkeypatch, key):
    r = make_client(monkeypatch, "old,new").get("/protected", headers={"X-API-Key": key})
    assert r.status_code == 200


def test_auth_disabled_when_no_keys(monkeypatch):
    assert make_client(monkeypatch, "").get("/protected").status_code == 200
