"""与 Agent 框架无关的 BeforeRun/AfterRun 生命周期语义。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from memory_mcp_agent.client import (
    CaptureSummary,
    MemoryHookClient,
    MemoryHookClientError,
)
from memory_mcp_agent.context import HookContext
from memory_mcp_agent.settings import MemoryHookSettings


@dataclass(frozen=True, slots=True)
class BeforeRunResult:
    """一次性注入的上下文或稳定的 fail-open 警告。"""

    memory_context: str | None
    recalled_count: int
    truncated: bool = False
    warning_code: str | None = None


@dataclass(frozen=True, slots=True)
class AfterRunResult:
    """顶层任务成功后返回一次的捕获回执。"""

    event_id: str
    status: str
    attempts: int
    replayed: bool = False
    capture_id: str | None = None
    summary: CaptureSummary | None = None
    created_memory_ids: tuple[str, ...] = ()
    pending_review_ids: tuple[str, ...] = ()
    failure_code: str | None = None
    warning_code: str | None = None


class MemoryHookRunConflictError(ValueError):
    """同一个顶层任务标识被不同 payload 重用。"""

    def __init__(self, phase: str) -> None:
        self.phase = phase
        super().__init__(f"{phase} run key was reused with a different payload")


@dataclass(frozen=True, slots=True)
class _BeforeTask:
    fingerprint: str
    task: asyncio.Task[BeforeRunResult]


@dataclass(frozen=True, slots=True)
class _AfterTask:
    fingerprint: str
    task: asyncio.Task[AfterRunResult]


class MemoryHookBridge:
    """按顶层任务去重 Hook，并执行有界 fail-open 重试。"""

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
        self._after_tasks: OrderedDict[
            tuple[str, str, str],
            _AfterTask,
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

    async def after_run_success(
        self,
        context: HookContext,
        *,
        user_input: str,
        final_output: str,
        observed_at: datetime | None = None,
    ) -> AfterRunResult:
        """顶层任务成功产生最终响应后捕获一次。"""

        if observed_at is not None and (
            observed_at.tzinfo is None or observed_at.utcoffset() is None
        ):
            raise ValueError("observed_at must be timezone-aware")
        fingerprint = _fingerprint(
            {
                "user_input": user_input,
                "final_output": final_output,
                "observed_at": observed_at.isoformat() if observed_at else None,
            }
        )
        async with self._lock:
            entry = self._after_tasks.get(context.run_key)
            if entry is None:
                resolved_time = observed_at or datetime.now(UTC)
                task = asyncio.create_task(
                    self._capture(
                        context,
                        user_input=user_input,
                        final_output=final_output,
                        observed_at=resolved_time,
                    )
                )
                task.add_done_callback(self._after_task_done)
                entry = _AfterTask(fingerprint=fingerprint, task=task)
                self._after_tasks[context.run_key] = entry
                self._trim_completed(self._after_tasks)
            elif entry.fingerprint != fingerprint:
                raise MemoryHookRunConflictError("AfterRun")
            else:
                self._after_tasks.move_to_end(context.run_key)
        return await asyncio.shield(entry.task)

    async def _recall(
        self,
        context: HookContext,
        user_input: str,
    ) -> BeforeRunResult:
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

    async def _capture(
        self,
        context: HookContext,
        *,
        user_input: str,
        final_output: str,
        observed_at: datetime,
    ) -> AfterRunResult:
        event_id = _event_id(context)
        final_error: MemoryHookClientError | None = None
        attempts = 0
        for attempt in range(1, self._settings.capture_max_attempts + 1):
            attempts = attempt
            try:
                response = await self._client.capture_completed_turn(
                    event_id=event_id,
                    profile_id=context.profile_id,
                    conversation_id=context.conversation_id,
                    turn_id=context.turn_id,
                    observed_at=observed_at,
                    user_input=user_input,
                    final_output=final_output,
                )
                return AfterRunResult(
                    event_id=event_id,
                    status=response.status,
                    attempts=attempt,
                    replayed=response.replayed,
                    capture_id=response.capture_id,
                    summary=response.summary,
                    created_memory_ids=response.created_memory_ids,
                    pending_review_ids=response.pending_review_ids,
                    failure_code=response.failure_code,
                )
            except MemoryHookClientError as exc:
                final_error = exc
                if exc.retryable and attempt < self._settings.capture_max_attempts:
                    await asyncio.sleep(self._settings.capture_retry_delay_seconds)
                    continue
                break
        assert final_error is not None
        if not self._settings.fail_open:
            raise final_error
        return AfterRunResult(
            event_id=event_id,
            status="warning",
            attempts=attempts,
            warning_code=final_error.code,
        )

    def _trim_completed(
        self,
        cache: OrderedDict[
            tuple[str, str, str],
            _BeforeTask | _AfterTask,
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

    def _after_task_done(self, _: asyncio.Task[AfterRunResult]) -> None:
        task = asyncio.create_task(self._trim_after_cache())
        self._maintenance_tasks.add(task)
        task.add_done_callback(self._maintenance_tasks.discard)

    async def _trim_before_cache(self) -> None:
        async with self._lock:
            self._trim_completed(self._before_tasks)

    async def _trim_after_cache(self) -> None:
        async with self._lock:
            self._trim_completed(self._after_tasks)


def _event_id(context: HookContext) -> str:
    identity = "\x1f".join(context.run_key)
    return f"memory-hook:{uuid5(NAMESPACE_URL, identity)}"


def _fingerprint(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
