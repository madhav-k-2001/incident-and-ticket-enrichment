"""Simple API-key authentication for the backend.

Clients send a key in the ``X-API-Key`` header. Valid keys come from the
``API_KEYS`` setting (comma-separated, so keys can be rotated by listing the
old and new key together). If no keys are configured, auth is disabled.
"""

from __future__ import annotations

import secrets
from typing import Optional

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from apps.backend.app.config import get_settings

API_KEY_HEADER = "X-API-Key"

_api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


async def require_api_key(api_key: Optional[str] = Security(_api_key_header)) -> None:
    """FastAPI dependency: reject the request unless it carries a valid API key."""
    valid_keys = get_settings().api_keys
    if not valid_keys:
        return  # auth disabled

    # Compare against every key (no early exit) in constant time.
    supplied = (api_key or "").encode()
    matched = False
    for key in valid_keys:
        matched |= secrets.compare_digest(supplied, key.encode())

    if not matched:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
