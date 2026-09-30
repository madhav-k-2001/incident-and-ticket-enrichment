"""Human-in-the-loop tool approval.

A tool listed under ``require_approval`` in the MCP server config makes the
Agents SDK pause the run instead of calling the tool. This module holds the
pieces that make that pause usable from a request/response API:

* ``PendingApproval``: what the user is asked to approve.
* ``ApprovalDecision``: the user's answer for one pending approval.
* ``PendingApprovalStore``: keeps the paused run (a serialized ``RunState``)
  between the request that paused it and the request that resumes it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol, Sequence

from agents.items import ToolApprovalItem


class ApprovalError(ValueError):
    """The decisions do not match the approvals the paused run is waiting on."""


@dataclass(frozen=True)
class PendingApproval:
    """One tool call waiting for the user's decision."""

    id: str
    tool_name: str
    server_name: Optional[str]
    arguments: Any

    @classmethod
    def from_item(cls, item: ToolApprovalItem) -> "PendingApproval":
        if not item.call_id:
            raise ApprovalError(f"Approval for tool {item.name!r} has no call id")
        raw_args = item.arguments
        try:
            arguments: Any = json.loads(raw_args) if raw_args else {}
        except (TypeError, ValueError):
            arguments = raw_args
        origin = item.tool_origin
        return cls(
            id=item.call_id,
            tool_name=item.name or "unknown",
            server_name=origin.mcp_server_name if origin else _hosted_server_label(item),
            arguments=arguments,
        )


@dataclass(frozen=True)
class ApprovalDecision:
    """The user's answer to one ``PendingApproval``.

    ``reason`` is sent back to the model when the call is rejected, so it can
    explain or take another route.
    """

    approval_id: str
    approved: bool
    reason: Optional[str] = None


def pending_approvals(interruptions: Sequence[ToolApprovalItem]) -> list[PendingApproval]:
    return [PendingApproval.from_item(i) for i in interruptions]


def _hosted_server_label(item: ToolApprovalItem) -> Optional[str]:
    # Hosted MCP approval requests carry the server label on the raw item.
    raw = item.raw_item
    label = raw.get("server_label") if isinstance(raw, dict) else getattr(raw, "server_label", None)
    return label if isinstance(label, str) else None


class PendingApprovalStore(Protocol):
    """Holds at most one paused run per chat session."""

    async def put(self, session_id: str, state: str) -> None: ...

    async def has(self, session_id: str) -> bool: ...

    async def pop(self, session_id: str) -> Optional[str]:
        """Return and remove the paused run, or ``None`` if there is none."""
        ...


class InMemoryPendingApprovalStore:
    """Process-local store with expiry.

    Paused runs are lost on restart and are not shared between workers; swap in
    a shared implementation of ``PendingApprovalStore`` (e.g. the chat
    database) to run more than one worker.
    """

    def __init__(self, ttl_seconds: float = 900.0) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, tuple[float, str]] = {}

    async def put(self, session_id: str, state: str) -> None:
        self._purge()
        self._entries[session_id] = (time.monotonic() + self._ttl, state)

    async def has(self, session_id: str) -> bool:
        self._purge()
        return session_id in self._entries

    async def pop(self, session_id: str) -> Optional[str]:
        self._purge()
        entry = self._entries.pop(session_id, None)
        return entry[1] if entry else None

    def _purge(self) -> None:
        now = time.monotonic()
        for key in [k for k, (expires, _) in self._entries.items() if expires <= now]:
            del self._entries[key]
