"""Configuration module for Simulator HTTP Client."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class SimulatorConfig:
    """Configuration for connecting to the Alarm & Ticket Simulator API."""

    base_url: str = field(
        default_factory=lambda: os.getenv("SIMULATOR_BASE_URL", "http://localhost:8000").rstrip("/")
    )
    auth_token: str = field(
        default_factory=lambda: os.getenv("SIMULATOR_API_TOKEN")
        or os.getenv("SIMULATOR_AUTH_TOKEN")
        or "demo-token"
    )
    timeout: float = field(
        default_factory=lambda: float(os.getenv("SIMULATOR_TIMEOUT", "10.0"))
    )
    max_retries: int = field(
        default_factory=lambda: int(os.getenv("SIMULATOR_MAX_RETRIES", "3"))
    )
    backoff_factor: float = field(
        default_factory=lambda: float(os.getenv("SIMULATOR_BACKOFF_FACTOR", "0.5"))
    )
    client_id: str = field(
        default_factory=lambda: os.getenv("SIMULATOR_CLIENT_ID", "mcp-client")
    )
    metadata_tag: str = field(
        default_factory=lambda: os.getenv("SIMULATOR_METADATA_TAG", "incident-enrichment")
    )

    def masked_token(self) -> str:
        """Return masked token representation."""
        if not self.auth_token:
            return ""
        if len(self.auth_token) <= 4:
            return "***"
        return f"{self.auth_token[:2]}***{self.auth_token[-2:]}"

    def __repr__(self) -> str:
        return (
            f"SimulatorConfig(base_url={self.base_url!r}, "
            f"auth_token={self.masked_token()!r}, "
            f"timeout={self.timeout}, "
            f"max_retries={self.max_retries}, "
            f"backoff_factor={self.backoff_factor}, "
            f"client_id={self.client_id!r}, "
            f"metadata_tag={self.metadata_tag!r})"
        )

    def __str__(self) -> str:
        return self.__repr__()
