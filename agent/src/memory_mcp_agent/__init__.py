"""用于把 Memory MCP 接入 Agent Runner 的框架无关 Hook。"""

from __future__ import annotations

from memory_mcp_agent.bridge import (
    AfterRunResult,
    BeforeRunResult,
    MemoryHookBridge,
    MemoryHookRunConflictError,
)
from memory_mcp_agent.client import (
    CaptureResponse,
    MemoryHookClientError,
    MemoryMcpClient,
    RecallResponse,
)
from memory_mcp_agent.context import HookContext
from memory_mcp_agent.hosts import (
    AgentHookAdapter,
    AgentHookInput,
    AgentHookInputError,
    AgentHookOutcome,
    AgentTurnEvent,
    parse_hook_input,
    render_command_hook_output,
)
from memory_mcp_agent.runner import HookedAgentRunner, RunnerResult
from memory_mcp_agent.settings import MemoryHookSettings
from memory_mcp_agent.state import (
    TurnState,
    TurnStateConflictError,
    TurnStateError,
    TurnStateStore,
)

__all__ = [
    "AfterRunResult",
    "AgentHookAdapter",
    "AgentHookInput",
    "AgentHookInputError",
    "AgentHookOutcome",
    "AgentTurnEvent",
    "BeforeRunResult",
    "CaptureResponse",
    "HookContext",
    "HookedAgentRunner",
    "MemoryHookBridge",
    "MemoryHookClientError",
    "MemoryHookRunConflictError",
    "MemoryHookSettings",
    "MemoryMcpClient",
    "RecallResponse",
    "RunnerResult",
    "TurnState",
    "TurnStateConflictError",
    "TurnStateError",
    "TurnStateStore",
    "parse_hook_input",
    "render_command_hook_output",
]
