"""Agent service built on the OpenAI Agents SDK.

Runs a single agent turn given the user message, an optional SDK ``Session``
(chat history) and an optional list of MCP servers (as produced by
``apps.backend.services.load_mcp_service``).
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any, AsyncIterator, Optional, Sequence, Union

from agents import Agent, Model, ModelSettings, Runner, RunResult, RunState, Session
from agents.mcp import MCPServer
from agents.stream_events import StreamEvent
from agents.tool import HostedMCPTool

from apps.backend.services.approval_service import (
    ApprovalDecision,
    PausedRun,
    pending_approvals,
)
from apps.backend.services.load_mcp_service import MCPAnyServer

DEFAULT_INSTRUCTIONS = (
    "You are an assistant that helps engineers enrich incidents and tickets. "
    "Use the available tools when they help answer the question."
)


class AgentService:
    """Runs an OpenAI Agents SDK agent for one chat turn.

    When a ``session`` is given the SDK loads prior history from it and stores
    the new turn back, so callers never manage history themselves.

    Local MCP servers (stdio / SSE / streamable HTTP) are connected for the
    duration of a single call and cleaned up afterwards. ``HostedMCPTool``
    entries are executed by the model provider, so they are passed to the
    agent as regular tools and need no connection.

    Tools marked ``require_approval`` in the MCP config pause the run: the
    result then has ``interruptions`` and no final output. Serialize it with
    ``result.to_state().to_string()``, ask the user, and continue with
    ``resume``.
    """

    def __init__(
        self,
        *,
        name: str = "Assistant",
        instructions: str = DEFAULT_INSTRUCTIONS,
        model: Optional[Union[str, Model]] = None,
        model_settings: Optional[ModelSettings] = None,
        max_turns: int = 10,
    ) -> None:
        self.name = name
        self.instructions = instructions
        self.model = model
        self.model_settings = model_settings
        self.max_turns = max_turns

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        user_message: str,
        session: Optional[Session] = None,
        mcp_servers: Optional[Sequence[MCPAnyServer]] = None,
    ) -> RunResult:
        """Run the agent to completion and return the SDK ``RunResult``.

        Use ``result.final_output`` for the reply text.
        """
        self._check_message(user_message)
        async with AsyncExitStack() as stack:
            agent = await self._build_agent(stack, mcp_servers)
            return await Runner.run(
                agent, user_message, session=session, max_turns=self.max_turns
            )

    async def resume(
        self,
        paused: PausedRun,
        decisions: Sequence[ApprovalDecision],
        session: Optional[Session] = None,
        mcp_servers: Optional[Sequence[MCPAnyServer]] = None,
    ) -> RunResult:
        """Continue a run that paused for tool approval.

        ``mcp_servers`` must be the same servers the run started with. Every
        pending approval needs exactly one decision (``ApprovalError``
        otherwise). Approved tools run; rejected
        ones are reported back to the model. The result may pause again if the
        agent then calls another tool that needs approval.
        """
        async with AsyncExitStack() as stack:
            agent = await self._build_agent(stack, mcp_servers)
            run_state = await self._restore_state(agent, paused, decisions)
            return await Runner.run(
                agent, run_state, session=session, max_turns=self.max_turns
            )

    async def run_stream(
        self,
        user_message: str,
        session: Optional[Session] = None,
        mcp_servers: Optional[Sequence[MCPAnyServer]] = None,
    ) -> AsyncIterator[Union[StreamEvent, PausedRun]]:
        """Run the agent and yield SDK stream events as they arrive.

        MCP servers stay connected until the stream is exhausted or closed; closing
        it early cancels the underlying run. If the run pauses for tool
        approval, a ``PausedRun`` is yielded last.
        """
        self._check_message(user_message)
        async with AsyncExitStack() as stack:
            agent = await self._build_agent(stack, mcp_servers)
            async for event in self._stream(agent, user_message, session):
                yield event

    async def resume_stream(
        self,
        paused: PausedRun,
        decisions: Sequence[ApprovalDecision],
        session: Optional[Session] = None,
        mcp_servers: Optional[Sequence[MCPAnyServer]] = None,
    ) -> AsyncIterator[Union[StreamEvent, PausedRun]]:
        """Streaming counterpart of ``resume``; events are as in ``run_stream``.

        Approval errors are raised on the first iteration, before any event.
        """
        async with AsyncExitStack() as stack:
            agent = await self._build_agent(stack, mcp_servers)
            run_state = await self._restore_state(agent, paused, decisions)
            async for event in self._stream(agent, run_state, session):
                yield event

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _build_agent(
        self,
        stack: AsyncExitStack,
        mcp_servers: Optional[Sequence[MCPAnyServer]],
    ) -> Agent:
        """Create the agent, connecting local MCP servers on ``stack``."""
        local_servers: list[MCPServer] = []
        hosted_tools: list[HostedMCPTool] = []
        for server in mcp_servers or []:
            if isinstance(server, HostedMCPTool):
                hosted_tools.append(server)
            else:
                local_servers.append(await stack.enter_async_context(server))

        kwargs: dict[str, Any] = {
            "name": self.name,
            "instructions": self.instructions,
            "mcp_servers": local_servers,
            "tools": hosted_tools,
        }
        if self.model is not None:
            kwargs["model"] = self.model
        if self.model_settings is not None:
            kwargs["model_settings"] = self.model_settings
        return Agent(**kwargs)

    async def _stream(
        self,
        agent: Agent,
        run_input: Union[str, RunState],
        session: Optional[Session],
    ) -> AsyncIterator[Union[StreamEvent, PausedRun]]:
        streamed = Runner.run_streamed(
            agent, run_input, session=session, max_turns=self.max_turns
        )
        try:
            async for event in streamed.stream_events():
                yield event
            if streamed.interruptions:
                yield PausedRun(
                    state=streamed.to_state().to_string(),
                    approvals=pending_approvals(streamed.interruptions),
                )
        finally:
            # No-op after normal completion; stops the model run if the
            # consumer stopped early (e.g. the HTTP client disconnected).
            streamed.cancel()

    async def _restore_state(
        self, agent: Agent, paused: PausedRun, decisions: Sequence[ApprovalDecision]
    ) -> RunState:
        decided = paused.check(decisions)
        run_state = await RunState.from_string(agent, paused.state)
        for item in run_state.get_interruptions():
            decision = decided[item.call_id]
            if decision.approved:
                run_state.approve(item)
            else:
                run_state.reject(item, rejection_message=decision.reason)
        return run_state

    @staticmethod
    def _check_message(user_message: str) -> None:
        if not user_message or not user_message.strip():
            raise ValueError("user_message must be a non-empty string")
