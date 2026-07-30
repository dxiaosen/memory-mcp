"""Framework-neutral hooks for adding Memory MCP to an Agent runner."""

from memory_mcp.memory_hooks.bridge import (
    AfterRunResult,
    BeforeRunResult,
    MemoryHookBridge,
    MemoryHookRunConflictError,
)
from memory_mcp.memory_hooks.client import (
    CaptureResponse,
    MemoryHookClientError,
    MemoryMcpClient,
    RecallResponse,
)
from memory_mcp.memory_hooks.context import HookContext
from memory_mcp.memory_hooks.runner import HookedAgentRunner, RunnerResult
from memory_mcp.memory_hooks.settings import MemoryHookSettings

__all__ = [
    "AfterRunResult",
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
]
