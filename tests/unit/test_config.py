import json

import pytest

from apps.backend.app.config import Settings, _to_asyncpg_url, get_settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Other tests import the app, which can load .env into os.environ; start from nothing."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


def settings(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+psycopg", "postgresql+asyncpg"])
def test_url_scheme_is_rewritten_for_asyncpg(scheme):
    assert _to_asyncpg_url(f"{scheme}://u:p@h:5432/db").startswith("postgresql+asyncpg://u:p@h:5432/db")


def test_sslmode_becomes_ssl_and_channel_binding_is_dropped():
    url = _to_asyncpg_url("postgresql://u:p@h/db?sslmode=require&channel_binding=require&application_name=x")
    assert url == "postgresql+asyncpg://u:p@h/db?ssl=require&application_name=x"


def test_database_url_wins_over_parts():
    s = settings(DATABASE_URL="postgres://a:b@neon/db?sslmode=require", POSTGRES_HOST="ignored")
    assert s.database_url == "postgresql+asyncpg://a:b@neon/db?ssl=require"


def test_database_url_built_from_parts_and_quotes_credentials():
    s = settings(
        POSTGRES_USER="us er", POSTGRES_PASSWORD="p@ss/word", POSTGRES_HOST="db", POSTGRES_PORT=6543, POSTGRES_DB="x"
    )
    assert s.database_url == "postgresql+asyncpg://us+er:p%40ss%2Fword@db:6543/x"


def test_database_url_from_parts_with_sslmode():
    assert settings(POSTGRES_SSLMODE="require").database_url.endswith("/copilot_db?ssl=require")


def test_defaults():
    s = settings()
    assert s.AGENT_MAX_TURNS == 10
    assert s.api_keys == []
    assert s.APPROVAL_TTL_SECONDS == 900.0
    assert s.DB_CREATE_TABLES is False


def test_env_vars_are_read_and_unknown_ones_ignored(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_TURNS", "3")
    monkeypatch.setenv("SOME_UNRELATED_VAR", "x")
    assert settings().AGENT_MAX_TURNS == 3


def test_mcp_spec_empty_when_unset():
    assert settings().mcp_servers_spec() == {"servers": []}
    assert settings(MCP_SERVERS_CONFIG="   ").mcp_servers_spec() == {"servers": []}


@pytest.mark.parametrize("raw", ['{"servers": []}', '  {"servers": []}'])
def test_mcp_spec_inline_json(raw):
    assert settings(MCP_SERVERS_CONFIG=raw).mcp_servers_spec() == {"servers": []}


def test_mcp_spec_from_file(tmp_path):
    spec = {"servers": [{"name": "a", "type": "stdio", "params": {"command": "x"}}]}
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    assert settings(MCP_SERVERS_CONFIG=str(path)).mcp_servers_spec() == spec


def test_mcp_spec_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        settings(MCP_SERVERS_CONFIG=str(tmp_path / "nope.json")).mcp_servers_spec()


def test_mcp_spec_bad_inline_json_raises():
    with pytest.raises(json.JSONDecodeError):
        settings(MCP_SERVERS_CONFIG="{not json").mcp_servers_spec()


def test_get_settings_is_cached():
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
