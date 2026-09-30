"""Offline test script for AgentService.

Uses a scripted fake model (no OpenAI key or network needed) and a real local
stdio MCP server (scripts/sample_mcp_server.py) loaded through
MCPServerConfigService.

Run from the project root:
    python scripts/test_agent_service.py
    pytest scripts/test_agent_service.py
    python scripts/test_agent_service.py --live [--model gpt-4.1-nano]

--live calls the real OpenAI API (needs OPENAI_API_KEY, read from .env).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.ext.asyncio import create_async_engine
from agents import Model, ModelResponse, set_tracing_disabled
from agents.usage import Usage
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from apps.backend.services.agent_service import AgentService
from apps.backend.services.chat_history_service import get_chat_session
from apps.backend.services.load_mcp_service import MCPServerConfigService

set_tracing_disabled(True)

SERVER_SCRIPT = str(ROOT / "scripts" / "sample_mcp_server.py")


class FakeModel(Model):
    """Scripted model: calls the `add` tool if offered, then echoes the result."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def _next_output(self, input, tools) -> list:
        self.calls.append({"input": input, "tools": tools})
        items = input if isinstance(input, list) else []
        tool_result = next(
            (i for i in items if i.get("type") == "function_call_output"), None
        )
        if tool_result is not None:
            text = f"tool said: {tool_result['output']}"
        else:
            add = next((t for t in tools if getattr(t, "name", "") == "add"), None)
            if add is not None:
                return [
                    ResponseFunctionToolCall(
                        id="fc_1",
                        call_id="call_1",
                        name="add",
                        arguments=json.dumps({"a": 2, "b": 3}),
                        type="function_call",
                    )
                ]
            text = "no tools used"
        return [
            ResponseOutputMessage(
                id="msg_1",
                role="assistant",
                status="completed",
                type="message",
                content=[
                    ResponseOutputText(text=text, type="output_text", annotations=[])
                ],
            )
        ]

    async def get_response(
        self, system_instructions, input, model_settings, tools, output_schema,
        handoffs, tracing, *, previous_response_id, conversation_id, prompt,
    ) -> ModelResponse:
        return ModelResponse(
            output=self._next_output(input, tools), usage=Usage(), response_id=None
        )

    async def stream_response(
        self, system_instructions, input, model_settings, tools, output_schema,
        handoffs, tracing, *, previous_response_id, conversation_id, prompt,
    ):
        response = Response(
            id="resp_1",
            created_at=0.0,
            model="fake",
            object="response",
            output=self._next_output(input, tools),
            parallel_tool_calls=False,
            tool_choice="auto",
            tools=[],
        )
        yield ResponseCompletedEvent(
            response=response, sequence_number=0, type="response.completed"
        )


def _stdio_servers():
    return MCPServerConfigService().load(
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


def _sqlite_engine():
    """Offline stand-in for PostgreSQL: any SQLAlchemy async engine works."""
    return create_async_engine("sqlite+aiosqlite:///:memory:")


async def test_session_history_persisted_per_session_id():
    model = FakeModel()
    svc = AgentService(model=model)
    engine = _sqlite_engine()
    try:
        first = get_chat_session("a", engine, create_tables=True)
        await svc.run("hello", first)
        # A new session object with the same id reloads history from the database.
        await svc.run("what next?", get_chat_session("a", engine))
        await svc.run("other user", get_chat_session("b", engine))
    finally:
        await engine.dispose()

    turn2 = model.calls[1]["input"]
    assert [i.get("content") for i in turn2 if i.get("role") == "user"] == [
        "hello",
        "what next?",
    ], turn2
    assert len(model.calls[2]["input"]) == 1, "session b saw session a's history"


async def test_local_mcp_tool_called_and_cleaned_up():
    model = FakeModel()
    servers = _stdio_servers()
    result = await AgentService(model=model).run("add 2 and 3", None, servers)

    assert "5" in result.final_output, result.final_output
    assert any(getattr(t, "name", "") == "add" for t in model.calls[0]["tools"])
    assert getattr(servers[0], "session", None) is None, "server not cleaned up"


async def test_hosted_mcp_tool_passed_as_tool():
    model = FakeModel()
    hosted = MCPServerConfigService().load(
        [{"name": "weather", "type": "hosted", "params": {"server_url": "https://example.com/mcp"}}]
    )
    await AgentService(model=model).run("hi", None, hosted)
    assert any(type(t).__name__ == "HostedMCPTool" for t in model.calls[0]["tools"])


async def test_stream_yields_events():
    model = FakeModel()
    servers = _stdio_servers()
    events = [e async for e in AgentService(model=model).run_stream("add", None, servers)]

    assert events, "no stream events"
    item_names = [getattr(e, "name", None) for e in events]
    assert "tool_called" in item_names and "tool_output" in item_names, item_names
    assert getattr(servers[0], "session", None) is None, "server not cleaned up"


async def test_empty_message_rejected():
    for bad in ("", "   "):
        try:
            await AgentService(model=FakeModel()).run(bad)
        except ValueError:
            continue
        raise AssertionError("expected ValueError for empty message")


TESTS = [
    test_session_history_persisted_per_session_id,
    test_local_mcp_tool_called_and_cleaned_up,
    test_hosted_mcp_tool_passed_as_tool,
    test_stream_yields_events,
    test_empty_message_rejected,
]


async def run_live(model: str) -> int:
    """Real-model smoke test: history recall + local MCP tool call."""
    from dotenv import load_dotenv

    load_dotenv(ROOT / "apps" / "backend" / ".env")
    set_tracing_disabled(True)
    svc = AgentService(
        model=model,
        instructions="Be terse. Use the add tool for any arithmetic.",
        max_turns=5,
    )
    engine = _sqlite_engine()
    session = get_chat_session("live", engine, create_tables=True)
    await svc.run("My name is Priya.", session)
    r1 = await svc.run("What is my name? Answer in one word.", session)
    await engine.dispose()
    print(f"[history] {r1.final_output!r}")
    ok1 = "priya" in r1.final_output.lower()

    servers = _stdio_servers()
    r2 = await svc.run("What is 1234 + 4321?", None, servers)
    print(f"[mcp tool] {r2.final_output!r}")
    called = [i for i in r2.new_items if i.type == "tool_call_item"]
    ok2 = "5555" in r2.final_output.replace(",", "") and bool(called)
    print(f"tool calls made: {len(called)}")

    print(f"\nlive: history={'OK' if ok1 else 'FAIL'} mcp={'OK' if ok2 else 'FAIL'}")
    return 0 if ok1 and ok2 else 1


async def _main() -> int:
    failed = 0
    for t in TESTS:
        try:
            await asyncio.wait_for(t(), timeout=60)
            print(f"PASS  {t.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="use the real OpenAI API")
    ap.add_argument("--model", default="gpt-4.1-nano")
    args = ap.parse_args()
    sys.exit(asyncio.run(run_live(args.model) if args.live else _main()))
