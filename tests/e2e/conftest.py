"""Fixtures for the end-to-end tests: they talk HTTP to an already running backend.

Nothing here imports the app. Point ``E2E_BASE_URL`` at a real deployment (for example
``docker compose up`` on http://localhost:9200) and run ``pytest -m e2e``. Without
``E2E_BASE_URL`` every test in this folder is skipped.

Environment:
    E2E_BASE_URL         base URL of the running backend (required)
    E2E_API_KEY          a valid X-API-Key, if the server has API_KEYS set
    E2E_TIMEOUT_SECONDS  per-request timeout, default 120 (LLM turns are slow)
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Iterator

import httpx
import pytest


@pytest.fixture(scope="session")
def base_url() -> str:
    url = os.environ.get("E2E_BASE_URL", "").strip().rstrip("/")
    if not url:
        pytest.skip("E2E_BASE_URL is not set: no real backend to test against")
    return url


@pytest.fixture(scope="session")
def api_key() -> str | None:
    return os.environ.get("E2E_API_KEY") or None


@pytest.fixture(scope="session")
def anonymous(base_url: str) -> Iterator[httpx.Client]:
    """A client that sends no API key."""
    timeout = float(os.environ.get("E2E_TIMEOUT_SECONDS", "120"))
    with httpx.Client(base_url=base_url, timeout=timeout) as c:
        try:
            c.get("/health")
        except httpx.TransportError as exc:
            pytest.fail(f"Backend at {base_url} is not reachable: {exc}", pytrace=False)
        yield c


@pytest.fixture(scope="session")
def client(base_url: str, api_key: str | None) -> Iterator[httpx.Client]:
    """A client authenticated with ``E2E_API_KEY`` (if set)."""
    timeout = float(os.environ.get("E2E_TIMEOUT_SECONDS", "120"))
    headers = {"X-API-Key": api_key} if api_key else {}
    with httpx.Client(base_url=base_url, headers=headers, timeout=timeout) as c:
        yield c


@pytest.fixture
def session_id() -> str:
    """A fresh chat session id; rows are left in the database under the ``e2e-`` prefix."""
    return f"e2e-{uuid.uuid4()}"


def _parse_sse(response: httpx.Response) -> list[tuple[str, dict]]:
    """Read an SSE response into ``[(event, data), ...]``, ignoring keep-alive comments."""
    events: list[tuple[str, dict]] = []
    for frame in response.text.split("\n\n"):
        name, data = None, None
        for line in frame.splitlines():
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
        if name is not None:
            events.append((name, data))
    return events


@pytest.fixture(scope="session")
def parse_sse():
    """``parse_sse(response)``: the SSE frames of a response as ``[(event, data), ...]``."""
    return _parse_sse
