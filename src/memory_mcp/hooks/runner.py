"""展示两个 Hook 接入位置的参考 Runner。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from memory_mcp.hooks.bridge import (
    AfterRunResult,
    BeforeRunResult,
    MemoryHookBridge,
)
from memory_mcp.hooks.context import HookContext

AgentCallable = Callable[[str, str | None], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class RunnerResult:
    final_output: str
    before_run: BeforeRunResult
    after_run: AfterRunResult


class HookedAgentRunner:
    """包装一次顶层 Agent 调用，不观察其内部步骤。"""

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
