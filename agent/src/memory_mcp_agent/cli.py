"""通用 Agent 主动记忆 command Hook 入口。

作为独立进程被各宿主（Codex/Claude Code 等）的 command Hook 调用，
从 stdin 读取单个事件 JSON，向 stdout 输出单个确定性 JSON。
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import sys
import traceback
from pathlib import Path
from typing import Any, TextIO

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
    """读取一个 Hook 事件并且只向 stdout 写入一个 JSON 对象。

    宿主（Claude Code/Codex）按 MCP 约定以 UTF-8 编码把事件 JSON 发到
    stdin。但 Windows 中文系统默认 stdin/stdout 编码是 GBK/CP936，直接用
    sys.stdin 会让 UTF-8 字节被按 GBK 解码，轻则 prompt 乱码，重则抛
    UnicodeDecodeError（ValueError 子类，逃逸 JSONDecodeError 捕获落到
    unexpected_error）。这里显式用 UTF-8 读写二进制缓冲，绕开系统默认编码。
    """

    stream = _utf8_stdin()
    output = asyncio.run(_run(stream))
    payload = json.dumps(
        output,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    _utf8_stdout_write(payload + "\n")


def _utf8_stdin() -> TextIO:
    """返回强制 UTF-8 解码的 stdin；回退到文本 stdin 以保持可测试性。"""

    raw = getattr(sys.stdin, "buffer", None)
    if raw is None:
        return sys.stdin
    return io.TextIOWrapper(raw, encoding="utf-8", errors="replace")


def _utf8_stdout_write(text: str) -> None:
    """以 UTF-8 写 stdout，绕开 Windows 默认 GBK 编码。"""

    raw = getattr(sys.stdout, "buffer", None)
    if raw is None:
        sys.stdout.write(text)
        return
    raw.write(text.encode("utf-8"))
    raw.flush()


async def _run(stream: TextIO) -> HookOutput:
    """解析单个 Hook 事件并执行归一化、召回或捕获，失败一律降级为 warning。"""
    raw_text: str | None = None
    try:
        raw_text = stream.read()
        payload = json.loads(raw_text)
        if not isinstance(payload, dict):
            return _warning_output("invalid_hook_input")
        hook_input = AgentHookInput.model_validate(payload)
    except UnicodeDecodeError as exc:
        # stdin 字节无法按当前编码解码（典型：Windows GBK 读 UTF-8）。
        # read() 本身就抛了，raw_text 还是 None，从异常对象取原始字节。
        raw_bytes = getattr(exc, "object", None)
        _configure_logging_safe()
        _log_failure(
            _event_name_from_raw(
                raw_bytes.decode("utf-8", errors="replace") if raw_bytes else None
            ),
            "stdin_decode_error",
            error=exc,
            raw_len=len(raw_bytes) if raw_bytes is not None else 0,
            raw_head=(
                raw_bytes[:200].decode("utf-8", errors="replace")
                if raw_bytes
                else None
            ),
            encoding=getattr(exc, "encoding", None),
            byte_position=getattr(exc, "start", None),
        )
        return _warning_output("stdin_decode_error")
    except (json.JSONDecodeError, ValidationError) as exc:
        _configure_logging_safe()
        _log_failure(
            _event_name_from_raw(raw_text),
            "invalid_hook_input",
            error=exc,
            raw_len=len(raw_text) if raw_text is not None else None,
            raw_head=raw_text[:200] if raw_text else None,
        )
        return _warning_output("invalid_hook_input")

    if not hook_input.supported:
        return {}

    _configure_logging()
    try:
        event = hook_input.normalize()
        if event is None:
            return {}
        settings = MemoryHookSettings()
    except ValidationError as exc:
        _log_failure(
            hook_input.hook_event_name,
            "configuration_error",
            error=exc,
        )
        return _warning_output("configuration_error")
    except AgentHookInputError as exc:
        code = str(exc) or "unexpected_error"
        _log_failure(hook_input.hook_event_name, code, error=exc)
        return _warning_output(code)

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
        code = str(exc) or "unexpected_error"
        _log_failure(hook_input.hook_event_name, code, error=exc)
        return _warning_output(code)
    except Exception as exc:
        # 兜底：打完整异常消息 + traceback，不再吞。
        _log_failure(
            hook_input.hook_event_name,
            "unexpected_error",
            error=exc,
        )
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


def _configure_logging_safe() -> None:
    """配置日志时不抛出，保证失败分支也能留痕。"""

    try:
        _configure_logging()
    except Exception:
        configure_logging(level="INFO", log_file=None, content=False)


def render_hook_output(output: HookOutput) -> str:
    """供测试和宿主包装器使用的确定性单行 JSON。"""

    return json.dumps(
        output,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _warning_output(code: str) -> HookOutput:
    return render_command_hook_output(AgentHookOutcome(warning_code=code))


def _log_failure(
    hook_event: str,
    code: str,
    *,
    error: BaseException | None = None,
    **extra: Any,
) -> None:
    """记录一次 hook 失败，打全：异常类型 + 完整消息 + 结构化摘要 + 完整 traceback。

    traceback 始终打到 stderr，不再需要环境开关。排障优先，先看到全貌。
    """

    fields: dict[str, Any] = {"error_code": code, "hook_event": hook_event}
    fields.update(extra)
    if error is not None:
        fields["error_type"] = type(error).__name__
        fields["error_message"] = str(error)
        detail = _validation_errors(error)
        if detail:
            fields["error_detail"] = detail
        cause = error.__cause__
        if cause is not None:
            fields["error_cause_type"] = type(cause).__name__
            fields["error_cause_message"] = str(cause)
    log_event(_LOGGER, logging.ERROR, "agent_hook.failed", **fields)
    if error is not None:
        traceback.print_exception(
            type(error),
            error,
            error.__traceback__,
            file=sys.stderr,
        )


def _event_name_from_raw(raw_text: str | None) -> str:
    """从原始 stdin 文本中尽力提取 hook_event_name，解析失败时也能用。"""

    if not raw_text:
        return "unknown"
    match = re.search(r'"hook_event_name"\s*:\s*"([^"]+)"', raw_text)
    return match.group(1) if match else "unknown"


def _validation_errors(error: BaseException) -> str | None:
    """从 pydantic ValidationError 提取「字段: 原因」摘要，非该类型返回 None。"""

    errors = getattr(error, "errors", None)
    if not callable(errors):
        return None
    try:
        items: list[dict[str, Any]] = list(errors())
    except Exception as exc:
        # errors() 自身抛异常（罕见）——记 DEBUG，避免完全静默。
        log_event(
            _LOGGER,
            logging.DEBUG,
            "validation_errors_extract_failed",
            source_error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return None
    if not items:
        return None
    parts: list[str] = []
    for err in items[:5]:
        loc = ".".join(str(p) for p in err.get("loc", ())) or "<root>"
        msg = str(err.get("msg", "")).strip()
        parts.append(f"{loc}: {msg}" if msg else loc)
    return " | ".join(parts) or None
