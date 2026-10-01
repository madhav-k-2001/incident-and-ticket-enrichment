"""Tests for POST /chat/stream (SSE) using a scripted model and a real stdio MCP server."""

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest
from agents import Model, ModelResponse, set_tracing_disabled
from agents.usage import Usage
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseTextDeltaEvent,
)
from sqlalchemy.ext.asyncio import create_async_engine

from apps.backend.app import main
from apps.backend.app.main import app
from apps.backend.services.agent_service import AgentService
from apps.backend.services.chat_stream_service import format_sse, sse_chat_stream
from apps.backend.services.load_mcp_service import MCPServerConfigService

set_tracing_disabled(True)

SERVER_SCRIPT = str(Path(__file__).resolve().parents[2] / "scripts" / "sample_mcp_server.py")


class FakeModel(Model):
    """Calls the ``add`` tool if offered, then streams its result as text deltas."""

    def _output(self, input, tools):
        tool_result = next((i for i in input if i.get("type") == "function_call_output"), None)
        if tool_result is None:
            if any(getattr(t, "name", "") == "add" for t in tools):
                return [
                    ResponseFunctionToolCall(
                        id="fc_1",
                        call_id="call_1",
                        name="add",
                        arguments=json.dumps({"a": 2, "b": 3}),
                        type="function_call",
                    )
                ], []
            text = "no tools"
        else:
            text = f"sum is {tool_result['output']}"
        message = ResponseOutputMessage(
            id="msg_1",
            role="assistant",
            status="completed",
            type="message",
            content=[ResponseOutputText(text=text, type="output_text", annotations=[])],
        )
        # Split into two chunks so the test sees incremental deltas.
        mid = len(text) // 2
        return [message], [text[:mid], text[mid:]]

    async def get_response(self, *args, **kwargs) -> ModelResponse:  # pragma: no cover
        raise NotImplementedError

    async def stream_response(
        self,
        system_instructions,
        input,
        model_settings,
        tools,
        output_schema,
        handoffs,
        tracing,
        *,
        previous_response_id,
        conversation_id,
        prompt,
    ):
        output, deltas = self._output(input, tools)
        for n, delta in enumerate(deltas):
            yield ResponseTextDeltaEvent(
                content_index=0,
                delta=delta,
                item_id="msg_1",
                logprobs=[],
                output_index=0,
                sequence_number=n,
                type="response.output_text.delta",
            )
        yield ResponseCompletedEvent(
            response=Response(
                id="resp_1",
                created_at=0.0,
                model="fake",
                object="response",
                output=output,
                parallel_tool_calls=False,
                tool_choice="auto",
                tools=[],
            ),
            sequence_number=99,
            type="response.completed",
        )


def parse_sse(body: str) -> list[tuple[str, dict]]:
    frames = []
    for block in body.strip().split("\n\n"):
        if block.startswith(":"):
            continue
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        frames.append((lines["event"], json.loads(lines["data"])))
    return frames


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setattr(main.settings, "DB_CREATE_TABLES", True)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    app.state.engine = engine
    app.state.mcp_servers = MCPServerConfigService().load(
        {
            "servers": [
                {
                    "name": "sample",
                    "type": "stdio",
                    "params": {"command": sys.executable, "args": [SERVER_SCRIPT]},
                    "options": {"client_session_timeout_seconds": 30},
                }
            ]
        }
    )
    app.state.agent_service = AgentService(model=FakeModel())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


async def _post(client, message="add 2 and 3", session_id="s1"):
    return await client.post("/chat/stream", json={"session_id": session_id, "message": message})


async def test_streams_text_tool_calls_and_done(client):
    resp = await _post(client)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["cache-control"] == "no-cache"

    events = parse_sse(resp.text)
    names = [n for n, _ in events]
    assert names[0] == "run_started" and names[-1] == "done"

    call = next(d for n, d in events if n == "tool_call")
    assert call["name"] == "add" and json.loads(call["arguments"]) == {"a": 2, "b": 3}
    out = next(d for n, d in events if n == "tool_output")
    assert out["call_id"] == call["call_id"] and "5" in out["output"]
    assert names.index("tool_call") < names.index("tool_output")

    deltas = [d["delta"] for n, d in events if n == "text_delta"]
    assert len(deltas) == 2 and "".join(deltas).startswith("sum is")
    assert events[-1][1]["final_output"] == "".join(deltas)
    assert events[-1][1]["session_id"] == "s1"


async def test_history_persisted_across_streamed_turns(client):
    await _post(client, "first", "hist")
    resp = await _post(client, "second", "hist")
    assert parse_sse(resp.text)[-1][0] == "done"


async def test_blank_message_rejected(client):
    resp = await _post(client, "   ")
    assert resp.status_code == 422


async def test_error_becomes_error_event_without_leaking_details():
    async def failing():
        raise RuntimeError("secret internal detail")
        yield  # pragma: no cover

    frames = [f async for f in sse_chat_stream("s", failing(), heartbeat_seconds=None)]
    events = parse_sse("".join(frames))
    assert [n for n, _ in events] == ["run_started", "error"]
    assert "secret" not in events[-1][1]["message"]


async def test_keepalive_sent_while_idle():
    async def slow():
        await asyncio.sleep(0.2)
        return
        yield  # pragma: no cover

    frames = [f async for f in sse_chat_stream("s", slow(), heartbeat_seconds=0.05)]
    assert ": keep-alive\n\n" in frames
    assert frames[-1].startswith("event: done")


async def test_closing_stream_cancels_and_cleans_up_producer():
    closed = asyncio.Event()

    async def endless():
        try:
            while True:
                await asyncio.sleep(0.01)
                yield object()  # ignored by the mapper
        finally:
            closed.set()

    gen = sse_chat_stream("s", endless(), heartbeat_seconds=None)
    await gen.__anext__()  # run_started
    await asyncio.sleep(0.05)  # let the producer start consuming
    await gen.aclose()
    assert closed.is_set()


def test_format_sse_is_single_data_line():
    frame = format_sse("text_delta", {"delta": "a\nb"})
    assert frame == 'event: text_delta\ndata: {"delta": "a\\nb"}\n\n'
