"""Agent service built on the OpenAI Agents SDK.

Runs a single agent turn given the user message, an optional SDK ``Session``
(chat history) and an optional list of MCP servers (as produced by
``apps.backend.services.load_mcp_service``).
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional, Sequence, Union

from agents import Agent, Model, ModelSettings, Runner, RunResult, RunState, Session
from agents.mcp import MCPServer
from agents.stream_events import StreamEvent
from agents.tool import HostedMCPTool

from apps.backend.services.approval_service import (
    ApprovalDecision,
    ApprovalError,
    PendingApproval,
    pending_approvals,
)
from apps.backend.services.load_mcp_service import MCPAnyServer

DEFAULT_INSTRUCTIONS = (
    "You are an assistant that helps engineers enrich incidents and tickets. "
    "Use the available tools when they help answer the question."
)


@dataclass(frozen=True)
class ApprovalRequiredEvent:
    """Last event of a stream that paused for tool approval.

    ``state`` is the serialized paused run; hand it to ``AgentService.resume``
    together with the user's decisions.
    """

    state: str
    approvals: list[PendingApproval]


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
        state: str,
        decisions: Sequence[ApprovalDecision],
        session: Optional[Session] = None,
        mcp_servers: Optional[Sequence[MCPAnyServer]] = None,
    ) -> RunResult:
        """Continue a run that paused for tool approval.

        ``state`` is the serialized paused run and ``mcp_servers`` must be the
        same servers it started with. Every pending approval needs exactly one
        decision (``ApprovalError`` otherwise). Approved tools run; rejected
        ones are reported back to the model. The result may pause again if the
        agent then calls another tool that needs approval.
        """
        async with AsyncExitStack() as stack:
            agent = await self._build_agent(stack, mcp_servers)
            run_state = await RunState.from_string(agent, state)
            self._apply_decisions(run_state, decisions)
            return await Runner.run(
                agent, run_state, session=session, max_turns=self.max_turns
            )

    async def run_stream(
        self,
        user_message: str,
        session: Optional[Session] = None,
        mcp_servers: Optional[Sequence[MCPAnyServer]] = None,
    ) -> AsyncIterator[Union[StreamEvent, ApprovalRequiredEvent]]:
        """Run the agent and yield SDK stream events as they arrive.

        MCP servers stay connected until the stream is exhausted or closed.
        If the run pauses for tool approval, an ``ApprovalRequiredEvent`` is
        yielded last.
        """
        self._check_message(user_message)
        async with AsyncExitStack() as stack:
            agent = await self._build_agent(stack, mcp_servers)
            streamed = Runner.run_streamed(
                agent, user_message, session=session, max_turns=self.max_turns
            )
            async for event in streamed.stream_events():
                yield event
            if streamed.interruptions:
                yield ApprovalRequiredEvent(
                    state=streamed.to_state().to_string(),
                    approvals=pending_approvals(streamed.interruptions),
                )

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

    @staticmethod
    def _apply_decisions(
        run_state: RunState, decisions: Sequence[ApprovalDecision]
    ) -> None:
        items = run_state.get_interruptions()
        pending = {a.id: item for a, item in zip(pending_approvals(items), items)}
        decided = {d.approval_id: d for d in decisions}
        if len(decided) != len(decisions):
            raise ApprovalError("Duplicate decision for the same approval")
        if unknown := decided.keys() - pending.keys():
            raise ApprovalError(f"No pending approval with id: {sorted(unknown)}")
        if missing := pending.keys() - decided.keys():
            raise ApprovalError(f"Missing decision for approval id: {sorted(missing)}")
        for approval_id, item in pending.items():
            decision = decided[approval_id]
            if decision.approved:
                run_state.approve(item)
            else:
                run_state.reject(item, rejection_message=decision.reason)

    @staticmethod
    def _check_message(user_message: str) -> None:
        if not user_message or not user_message.strip():
            raise ValueError("user_message must be a non-empty string")
