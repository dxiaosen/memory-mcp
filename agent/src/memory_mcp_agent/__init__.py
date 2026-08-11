"""用于把 Memory MCP 接入 Agent Runner 的框架无关 Hook。"""

from __future__ import annotations

from memory_mcp_agent.bridge import (
    BeforeRunResult,
    MemoryHookBridge,
    MemoryHookRunConflictError,
)
from memory_mcp_agent.client import (
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
from memory_mcp_agent.settings import MemoryHookSettings
from memory_mcp_agent.state import (
    TurnStateError,
    TurnStateStore,
)

__all__ = [
    "AgentHookAdapter",
    "AgentHookInput",
    "AgentHookInputError",
    "AgentHookOutcome",
    "AgentTurnEvent",
    "BeforeRunResult",
    "HookContext",
    "MemoryHookBridge",
    "MemoryHookClientError",
    "MemoryHookRunConflictError",
    "MemoryHookSettings",
    "MemoryMcpClient",
    "RecallResponse",
    "TurnStateError",
    "TurnStateStore",
    "parse_hook_input",
    "render_command_hook_output",
]
