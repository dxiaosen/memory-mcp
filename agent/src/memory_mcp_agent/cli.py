"""通用 Agent 主动记忆 command Hook。"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError

from memory_mcp_agent.bridge import MemoryHookBridge
from memory_mcp_agent.client import MemoryMcpClient
from memory_mcp_agent.hosts import (
    AgentHookAdapter,
    AgentHookInput,
    AgentHookInputError,
    AgentHookOutcome,
    HookOutput,
    render_command_hook_output,
)
from memory_mcp_agent.logging import configure_logging, log_event
from memory_mcp_agent.settings import MemoryHookSettings
from memory_mcp_agent.state import TurnStateError, TurnStateStore

_LOGGER = logging.getLogger(__name__)


def main() -> None:
    """读取一个 Hook 事件并且只向 stdout 写入一个 JSON 对象。"""

    output = asyncio.run(_run(sys.stdin))
    sys.stdout.write(
        json.dumps(
            output,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    sys.stdout.write("\n")


async def _run(stream: TextIO) -> HookOutput:
    try:
        payload = json.load(stream)
        if not isinstance(payload, dict):
            return _warning_output("invalid_hook_input")
        hook_input = AgentHookInput.model_validate(payload)
    except (json.JSONDecodeError, ValidationError):
        return _warning_output("invalid_hook_input")

    if not hook_input.supported:
        return {}

    _configure_logging()
    try:
        event = hook_input.normalize()
        if event is None:
            return {}
        settings = MemoryHookSettings()
    except ValidationError:
        _log_failure(hook_input.hook_event_name, "configuration_error")
        return _warning_output("configuration_error")
    except AgentHookInputError as exc:
        _log_failure(hook_input.hook_event_name, str(exc))
        return _warning_output(str(exc))

    try:
        async with MemoryMcpClient(settings) as client:
            bridge = MemoryHookBridge(client, settings)
            state = TurnStateStore.for_working_directory(event.cwd)
            outcome = await AgentHookAdapter(
                bridge,
                settings,
                state,
            ).handle(event)
            return render_command_hook_output(outcome)
    except (AgentHookInputError, TurnStateError) as exc:
        _log_failure(hook_input.hook_event_name, str(exc))
        return _warning_output(str(exc))
    except Exception:
        _log_failure(hook_input.hook_event_name, "unexpected_error")
        return _warning_output("unexpected_error")


def _configure_logging() -> None:
    log_file = Path.cwd() / ".memory-mcp" / "logs" / "agent-hook.log"
    try:
        configure_logging(
            level="INFO",
            log_file=log_file,
            content=False,
        )
    except OSError:
        configure_logging(
            level="INFO",
            log_file=None,
            content=False,
        )


def render_hook_output(output: HookOutput) -> str:
    """供测试和宿主包装器使用的确定性单行 JSON。"""

    return json.dumps(
        output,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _warning_output(code: str) -> HookOutput:
    return render_command_hook_output(AgentHookOutcome(warning_code=code))


def _log_failure(hook_event: str, code: str) -> None:
    log_event(
        _LOGGER,
        logging.ERROR,
        "agent_hook.failed",
        error_code=code,
        hook_event=hook_event,
    )
