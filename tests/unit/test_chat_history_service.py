import pytest
from agents.extensions.memory import SQLAlchemySession
from sqlalchemy.ext.asyncio import create_async_engine

from apps.backend.app.config import Settings
from apps.backend.services.chat_history_service import create_engine, get_chat_session


async def test_create_engine_uses_settings_url_and_pool():
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgres://u:p@localhost:5432/db?sslmode=require",
        DB_POOL_SIZE=3,
        DB_MAX_OVERFLOW=4,
        DB_POOL_TIMEOUT=5,
    )
    engine = create_engine(settings)  # lazy: no connection is made
    try:
        assert engine.url.drivername == "postgresql+asyncpg"
        assert engine.url.host == "localhost"
        assert engine.url.database == "db"
        assert engine.pool.size() == 3
        assert engine.pool._max_overflow == 4
    finally:
        await engine.dispose()


@pytest.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    yield engine
    await engine.dispose()


async def test_get_chat_session_returns_session_bound_to_id(engine):
    session = get_chat_session("s1", engine)
    assert isinstance(session, SQLAlchemySession)
    assert session.session_id == "s1"


async def test_history_round_trip_and_isolation_between_sessions(engine):
    a = get_chat_session("a", engine, create_tables=True)
    await a.add_items([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}])
    b = get_chat_session("b", engine, create_tables=True)

    assert [i["content"] for i in await a.get_items()] == ["hi", "hello"]
    assert await b.get_items() == []


async def test_clear_session_only_clears_that_conversation(engine):
    a = get_chat_session("a", engine, create_tables=True)
    b = get_chat_session("b", engine, create_tables=True)
    await a.add_items([{"role": "user", "content": "x"}])
    await b.add_items([{"role": "user", "content": "y"}])
    await a.clear_session()
    assert await a.get_items() == []
    assert len(await b.get_items()) == 1
