"""Reference runner showing where the two hooks belong."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from memory_mcp.memory_hooks.bridge import (
    AfterRunResult,
    BeforeRunResult,
    MemoryHookBridge,
)
from memory_mcp.memory_hooks.context import HookContext

AgentCallable = Callable[[str, str | None], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class RunnerResult:
    final_output: str
    before_run: BeforeRunResult
    after_run: AfterRunResult


class HookedAgentRunner:
    """Wrap one top-level Agent call without observing its internal steps."""

    def __init__(
        self,
        bridge: MemoryHookBridge,
        agent: AgentCallable,
    ) -> None:
        self._bridge = bridge
        self._agent = agent

    async def run(
        self,
        context: HookContext,
        user_input: str,
    ) -> RunnerResult:
        recalled = await self._bridge.before_run(context, user_input)
        final_output = await self._agent(user_input, recalled.memory_context)
        captured = await self._bridge.after_run_success(
            context,
            user_input=user_input,
            final_output=final_output,
        )
        return RunnerResult(
            final_output=final_output,
            before_run=recalled,
            after_run=captured,
        )
