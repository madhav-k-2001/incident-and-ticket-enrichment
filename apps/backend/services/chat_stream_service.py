"""Server-Sent Events for the streaming chat endpoint.

Translates the Agents SDK stream events (raw model events and run items) into a
small, stable set of SSE events a frontend can render without knowing anything
about the SDK. The wire protocol is documented in ``docs/chat-stream-protocol.md``.

Events (``event:`` name -> JSON ``data:`` payload):

    run_started       {session_id}
    agent_updated     {agent}
    text_delta        {delta}                             incremental answer text
    reasoning_delta   {delta}                             incremental reasoning summary
    message           {text}                              one complete assistant message
    tool_call         {call_id, name, arguments, server_label?}
    tool_output       {call_id, output, error?}
    approval_required {session_id, approvals}             last, instead of done, when a tool
                                                          needs the user's approval
    done              {session_id, final_output}          always last on success
    error             {type, message}                     always last on failure
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional, Union

from agents import ItemHelpers
from agents.exceptions import AgentsException
from agents.items import MessageOutputItem, ToolCallItem, ToolCallOutputItem
from agents.stream_events import (
    AgentUpdatedStreamEvent,
    RawResponsesStreamEvent,
    RunItemStreamEvent,
    StreamEvent,
)

from apps.backend.services.approval_service import PausedRun

logger = logging.getLogger(__name__)

# SSE comment line; ignored by clients but keeps proxies from closing an idle
# connection while a slow tool call is running.
KEEPALIVE = ": keep-alive\n\n"

# Response headers for an SSE stream (X-Accel-Buffering stops nginx buffering it).
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

ChatEvent = tuple[str, dict[str, Any]]


def format_sse(event: str, data: dict[str, Any]) -> str:
    """Encode one SSE frame. JSON has no raw newlines, so one ``data:`` line is enough."""
    return f"event: {event}\ndata: {json.dumps(data, default=str, ensure_ascii=False)}\n\n"


def _get(obj: Any, key: str) -> Any:
    """Read ``key`` from a raw SDK item that may be a dict or a pydantic model."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


class ChatEventMapper:
    """Maps SDK ``StreamEvent`` objects to ``(event_name, payload)`` pairs.

    Stateful only in that it remembers the last complete assistant message, which
    becomes ``final_output`` on the closing ``done`` event, and the approvals of a
    paused run, which close the stream with ``approval_required`` instead.
    """

    def __init__(self) -> None:
        self.final_output: str = ""
        self.approvals: Optional[list[dict[str, Any]]] = None

    def map(self, event: Union[StreamEvent, PausedRun]) -> list[ChatEvent]:
        if isinstance(event, PausedRun):
            self.approvals = [a.to_dict() for a in event.approvals]
            return []
        if isinstance(event, RawResponsesStreamEvent):
            return self._map_raw(event)
        if isinstance(event, RunItemStreamEvent):
            return self._map_item(event)
        if isinstance(event, AgentUpdatedStreamEvent):
            return [("agent_updated", {"agent": event.new_agent.name})]
        return []

    @staticmethod
    def _map_raw(event: RawResponsesStreamEvent) -> list[ChatEvent]:
        data = event.data
        kind = getattr(data, "type", None)
        if kind == "response.output_text.delta":
            return [("text_delta", {"delta": data.delta})]
        if kind == "response.reasoning_summary_text.delta":
            return [("reasoning_delta", {"delta": data.delta})]
        return []

    def _map_item(self, event: RunItemStreamEvent) -> list[ChatEvent]:
        item = event.item

        if isinstance(item, MessageOutputItem):
            text = ItemHelpers.text_message_output(item)
            self.final_output = text
            return [("message", {"text": text})]

        if isinstance(item, ToolCallItem):
            raw = item.raw_item
            payload: dict[str, Any] = {
                "call_id": item.call_id,
                "name": item.tool_name,
                "arguments": _stringify(_get(raw, "arguments") or ""),
            }
            events: list[ChatEvent] = []
            server_label = _get(raw, "server_label")
            if server_label:
                payload["server_label"] = server_label
            events.append(("tool_call", payload))
            # Hosted MCP tools run at the model provider: the result is on the call item.
            if _get(raw, "type") == "mcp_call" and (
                _get(raw, "output") is not None or _get(raw, "error") is not None
            ):
                out: dict[str, Any] = {
                    "call_id": item.call_id,
                    "output": _stringify(_get(raw, "output") or ""),
                }
                if _get(raw, "error") is not None:
                    out["error"] = _stringify(_get(raw, "error"))
                events.append(("tool_output", out))
            return events

        if isinstance(item, ToolCallOutputItem):
            return [
                (
                    "tool_output",
                    {"call_id": item.call_id, "output": _stringify(item.output)},
                )
            ]

        return []


def _error_payload(exc: BaseException) -> dict[str, str]:
    # SDK errors (e.g. MaxTurnsExceeded, guardrail tripwires) are safe to show;
    # anything else may carry internals, so it is logged and replaced.
    if isinstance(exc, AgentsException):
        return {"type": type(exc).__name__, "message": str(exc)}
    return {"type": "InternalError", "message": "The agent run failed."}


async def sse_chat_stream(
    session_id: str,
    events: AsyncIterator[Union[StreamEvent, PausedRun]],
    *,
    heartbeat_seconds: Optional[float] = 15.0,
) -> AsyncIterator[str]:
    """Yield SSE frames for one agent run.

    ``events`` is consumed in a single background task so the agent's async
    context managers (MCP server connections) are entered and exited in the same
    task, even when the client disconnects mid-run and this generator is
    cancelled. Failures become a final ``error`` event because the HTTP status
    was already sent with the first byte.
    """
    mapper = ChatEventMapper()
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=256)

    async def produce() -> None:
        try:
            async for event in events:
                for name, payload in mapper.map(event):
                    await queue.put(("event", (name, payload)))
            await queue.put(("end", None))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Chat stream failed for session %s", session_id)
            await queue.put(("error", exc))

    producer = asyncio.create_task(produce())
    try:
        yield format_sse("run_started", {"session_id": session_id})
        while True:
            try:
                kind, value = await asyncio.wait_for(queue.get(), heartbeat_seconds)
            except asyncio.TimeoutError:
                yield KEEPALIVE
                continue
            if kind == "event":
                yield format_sse(*value)
            elif kind == "end":
                if mapper.approvals is not None:
                    yield format_sse(
                        "approval_required",
                        {"session_id": session_id, "approvals": mapper.approvals},
                    )
                else:
                    yield format_sse(
                        "done",
                        {"session_id": session_id, "final_output": mapper.final_output},
                    )
                return
            else:
                yield format_sse("error", _error_payload(value))
                return
    finally:
        # Client gone or run finished: stop the agent run and let it clean up.
        if not producer.done():
            producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
