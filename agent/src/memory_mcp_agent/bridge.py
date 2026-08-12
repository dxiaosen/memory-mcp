"""与 Agent 框架无关的 BeforeRun/AfterRun 生命周期语义。

BeforeRun 召回注入；AfterRun 经 Stop hook 强制调 ``capture_completed_turn``
入队（服务端队列异步抽取）。入队毫秒级返回，失败走 fail-open——不入 outbox
不重投，下一轮 Stop 用同 conversation_id+turn_id 再入队时服务端 event_id 幂等
兜底（上轮可能已入队、只是响应丢了 → replay）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from memory_mcp_agent.client import (
    MemoryHookClient,
    MemoryHookClientError,
)
from memory_mcp_agent.context import HookContext
from memory_mcp_agent.logging import log_event
from memory_mcp_agent.settings import MemoryHookSettings

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BeforeRunResult:
    """BeforeRun 的结果：成功时注入召回上下文，失败时返回 fail-open 警告。"""

    memory_context: str | None
    recalled_count: int
    truncated: bool = False
    warning_code: str | None = None


@dataclass(frozen=True, slots=True)
class AfterRunResult:
    """AfterRun 入队 capture 的结果：status=pending 成功，失败时返回 fail-open 警告。"""

    status: str
    replayed: bool = False
    capture_id: str | None = None
    warning_code: str | None = None
    failure_code: str | None = None


class MemoryHookRunConflictError(ValueError):
    """同一个顶层任务标识被不同 payload 重用时抛出，用于保护幂等语义。"""

    def __init__(self, phase: str) -> None:
        self.phase = phase
        super().__init__(f"{phase} run key was reused with a different payload")


@dataclass(frozen=True, slots=True)
class _BeforeTask:
    fingerprint: str
    task: asyncio.Task[BeforeRunResult]


class MemoryHookBridge:
    """按顶层任务去重 BeforeRun 召回，并执行有界 fail-open 重试。

    同一个 run_key 的 BeforeRun 只执行一次；若调用方以不同 payload 重用同一
    run_key，则抛出 MemoryHookRunConflictError 以保证幂等。当 fail_open 开启时，
    记忆服务的异常不会中断上层 Agent 任务。
    """

    def __init__(
        self,
        client: MemoryHookClient,
        settings: MemoryHookSettings,
    ) -> None:
        self._client = client
        self._settings = settings
        self._lock = asyncio.Lock()
        self._maintenance_tasks: set[asyncio.Task[None]] = set()
        self._before_tasks: OrderedDict[
            tuple[str, str, str],
            _BeforeTask,
        ] = OrderedDict()

    async def before_run(
        self,
        context: HookContext,
        user_input: str,
    ) -> BeforeRunResult:
        """一个顶层用户任务开始前最多召回一次。"""

        fingerprint = _fingerprint(
            {
                "subject": context.subject,
                "task_intent": context.task_intent,
                "user_input": user_input,
            }
        )
        async with self._lock:
            entry = self._before_tasks.get(context.run_key)
            if entry is None:
                task = asyncio.create_task(self._recall(context, user_input))
                task.add_done_callback(self._before_task_done)
                entry = _BeforeTask(fingerprint=fingerprint, task=task)
                self._before_tasks[context.run_key] = entry
                self._trim_completed(self._before_tasks)
            elif entry.fingerprint != fingerprint:
                raise MemoryHookRunConflictError("BeforeRun")
            else:
                self._before_tasks.move_to_end(context.run_key)
        return await asyncio.shield(entry.task)

    async def _recall(
        self,
        context: HookContext,
        user_input: str,
    ) -> BeforeRunResult:
        """执行一次召回；fail_open 关闭时向上抛出，开启时返回警告。"""
        try:
            response = await self._client.recall_memory(
                profile_id=context.profile_id,
                query=user_input,
                subject=context.subject,
                task_intent=context.task_intent,
                max_items=self._settings.recall_max_items,
                token_budget=self._settings.recall_token_budget,
            )
        except MemoryHookClientError as exc:
            if not self._settings.fail_open:
                raise
            # fail-open 降级为 warning 前必须记全异常链，否则召回失败原因丢失。
            cause = exc.__cause__
            log_event(
                _LOGGER,
                logging.WARNING,
                "agent_hook.recall.fail_open",
                error_code=exc.code,
                retryable=exc.retryable,
                error_type=type(exc).__name__,
                error_message=str(exc),
                cause_type=type(cause).__name__ if cause else None,
                cause_message=str(cause) if cause else None,
            )
            return BeforeRunResult(
                memory_context=None,
                recalled_count=0,
                warning_code=exc.code,
            )
        memory_context = (
            response.rendered_context.strip()
            if response.items and response.rendered_context.strip()
            else None
        )
        return BeforeRunResult(
            memory_context=memory_context,
            recalled_count=len(response.items),
            truncated=response.truncated,
        )

    async def after_run_success(
        self,
        context: HookContext,
        *,
        user_input: str,
        final_output: str,
        subject_hint: str | None = None,
        document_messages: list[dict[str, Any]] | None = None,
    ) -> AfterRunResult:
        """Stop hook 强制入队 capture：毫秒级返回 pending，失败走 fail-open。

        入队幂等键由服务端 event_id（从 owner+conversation+turn 派生）保证，
        本地不做去重/重试——入队失败即 fail-open 警告，下一轮 Stop 幂等兜底。
        ``document_messages`` 由 Host Adapter 从 transcript 提取的文件/工具
        来源消息，透传给服务端补全 document provenance。
        """

        try:
            response = await self._client.capture_completed_turn(
                profile_id=context.profile_id,
                conversation_id=context.conversation_id,
                turn_id=context.turn_id,
                user_input=user_input,
                final_output=final_output,
                subject_hint=subject_hint,
                document_messages=document_messages,
            )
        except MemoryHookClientError as exc:
            if not self._settings.fail_open:
                raise
            cause = exc.__cause__
            log_event(
                _LOGGER,
                logging.WARNING,
                "agent_hook.capture.fail_open",
                error_code=exc.code,
                retryable=exc.retryable,
                error_type=type(exc).__name__,
                error_message=str(exc),
                cause_type=type(cause).__name__ if cause else None,
                cause_message=str(cause) if cause else None,
            )
            return AfterRunResult(
                status="failed",
                warning_code=exc.code,
            )
        log_event(
            _LOGGER,
            logging.INFO,
            "agent_hook.capture.completed",
            capture_id=response.capture_id,
            replayed=response.replayed,
            status=response.status,
        )
        return AfterRunResult(
            status=response.status,
            replayed=response.replayed,
            capture_id=response.capture_id,
            failure_code=response.failure_code,
        )

    def _trim_completed(
        self,
        cache: OrderedDict[
            tuple[str, str, str],
            _BeforeTask,
        ],
    ) -> None:
        """限制已保留回执数量，但不取消正在执行的 Hook。"""

        limit = self._settings.run_cache_max_entries
        while len(cache) > limit:
            completed_key = next(
                (key for key, entry in cache.items() if entry.task.done()),
                None,
            )
            if completed_key is None:
                return
            del cache[completed_key]

    def _before_task_done(self, _: asyncio.Task[BeforeRunResult]) -> None:
        task = asyncio.create_task(self._trim_before_cache())
        self._maintenance_tasks.add(task)
        task.add_done_callback(self._maintenance_tasks.discard)

    async def _trim_before_cache(self) -> None:
        async with self._lock:
            self._trim_completed(self._before_tasks)


def _fingerprint(payload: dict[str, object]) -> str:
    """对 payload 计算稳定指纹，用于检测 run_key 是否被不同 payload 重用。"""

    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
