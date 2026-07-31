"""用于把 Memory MCP 接入 Agent Runner 的框架无关 Hook。"""

from memory_mcp.hooks.bridge import (
    AfterRunResult,
    BeforeRunResult,
    MemoryHookBridge,
    MemoryHookRunConflictError,
)
from memory_mcp.hooks.client import (
    CaptureResponse,
    MemoryHookClientError,
    MemoryMcpClient,
    RecallResponse,
)
from memory_mcp.hooks.context import HookContext
from memory_mcp.hooks.hosts import (
    AgentHookAdapter,
    AgentHookInput,
    AgentHookInputError,
    AgentHookOutcome,
    AgentTurnEvent,
    parse_hook_input,
    render_command_hook_output,
)
from memory_mcp.hooks.runner import HookedAgentRunner, RunnerResult
from memory_mcp.hooks.settings import MemoryHookSettings
from memory_mcp.hooks.state import (
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
