"""Typed domain exceptions for Simulator API client interactions."""

from __future__ import annotations

from typing import Any, Optional


class SimulatorClientError(Exception):
    """Base domain exception for all simulator client errors."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SimulatorConnectionError(SimulatorClientError):
    """Raised when connection fails or timeouts occur after exhausting retries."""


class SimulatorAPIError(SimulatorClientError):
    """Base domain exception for HTTP 4xx and 5xx responses from the simulator."""

    def __init__(
        self,
        message: str,
        status_code: int,
        response_body: Optional[Any] = None,
    ) -> None:
        super().__init__(f"[{status_code}] {message}")
        self.status_code = status_code
        self.response_body = response_body


class NotFoundError(SimulatorAPIError):
    """Raised when a requested resource (asset, alarm, ticket) is not found (HTTP 404)."""


class ValidationError(SimulatorAPIError):
    """Raised when request payload or parameters fail validation (HTTP 400 or 422)."""


class AuthenticationError(SimulatorAPIError):
    """Raised when authentication or authorization fails (HTTP 401 or 403)."""


class ConflictError(SimulatorAPIError):
    """Raised when request conflicts with current server state (HTTP 409)."""


class SimulatorServerError(SimulatorAPIError):
    """Raised on 5xx internal server errors from the simulator."""
