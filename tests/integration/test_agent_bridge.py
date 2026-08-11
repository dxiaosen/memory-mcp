import asyncio

import anyio
import pytest
from memory_mcp_agent import (
    HookContext,
    MemoryHookBridge,
    MemoryHookClientError,
    MemoryHookRunConflictError,
    MemoryHookSettings,
    RecallResponse,
)
from memory_mcp_agent.client import RecalledItem


def _settings(**overrides: object) -> MemoryHookSettings:
    values: dict[str, object] = {
        "mcp_url": "http://127.0.0.1:8765/mcp",
        "bearer_token": "configured-token",
        "_env_file": None,
    }
    values.update(overrides)
    return MemoryHookSettings(**values)


def _recall_response(*, with_memory: bool = True) -> RecallResponse:
    items = (
        (
            RecalledItem(
                memory_id="memory-1",
                revision_id="revision-1",
                content="项目周报默认使用表格",
            ),
        )
        if with_memory
        else ()
    )
    return RecallResponse(
        ok=True,
        request_id="request-1",
        items=items,
        rendered_context=(
            "<memory-context>项目周报默认使用表格</memory-context>"
            if with_memory
            else ""
        ),
        estimated_tokens=10 if with_memory else 0,
        token_budget=600,
        truncated=False,
    )


class _FakeClient:
    def __init__(
        self,
        *,
        recall: RecallResponse | Exception | None = None,
    ) -> None:
        self.recall = recall or _recall_response()
        self.recall_calls = 0

    async def recall_memory(self, **_: object) -> RecallResponse:
        self.recall_calls += 1
        await asyncio.sleep(0)
        if isinstance(self.recall, Exception):
            raise self.recall
        return self.recall


def _context() -> HookContext:
    return HookContext(
        conversation_id="conversation-1",
        turn_id="turn-1",
        subject="weekly-report",
    )


def test_before_run_executes_at_most_once_per_top_level_turn() -> None:
    """同一个 run_key 的 BeforeRun 只执行一次召回（幂等去重）。"""

    async def profile_id() -> None:
        client = _FakeClient()
        bridge = MemoryHookBridge(client, _settings())
        before = await asyncio.gather(
            bridge.before_run(_context(), "项目周报"),
            bridge.before_run(_context(), "项目周报"),
        )

        assert before[0] == before[1]
        assert client.recall_calls == 1

    anyio.run(profile_id)


def test_same_run_key_with_different_payload_is_rejected() -> None:
    async def profile_id() -> None:
        bridge = MemoryHookBridge(_FakeClient(), _settings())
        await bridge.before_run(_context(), "项目周报")
        with pytest.raises(MemoryHookRunConflictError, match="BeforeRun"):
            await bridge.before_run(_context(), "另一个请求")

    anyio.run(profile_id)


def test_completed_run_cache_is_bounded_without_cancelling_work() -> None:
    async def profile_id() -> None:
        client = _FakeClient()
        bridge = MemoryHookBridge(
            client,
            _settings(run_cache_max_entries=2),
        )
        for index in range(3):
            await bridge.before_run(
                HookContext(
                    conversation_id="conversation-1",
                    turn_id=f"turn-{index}",
                ),
                f"input-{index}",
            )

        await bridge.before_run(
            HookContext(
                conversation_id="conversation-1",
                turn_id="turn-0",
            ),
            "input-0",
        )
        assert client.recall_calls == 4
        await asyncio.sleep(0)
        assert len(bridge._before_tasks) <= 2

    anyio.run(profile_id)


def test_empty_recall_injects_no_placeholder_context() -> None:
    async def profile_id() -> None:
        client = _FakeClient(recall=_recall_response(with_memory=False))
        result = await MemoryHookBridge(client, _settings()).before_run(
            _context(),
            "没有相关记忆",
        )

        assert result.memory_context is None
        assert result.recalled_count == 0

    anyio.run(profile_id)


def test_fail_closed_re_raises_hook_error() -> None:
    async def profile_id() -> None:
        error = MemoryHookClientError("not_authorized")
        client = _FakeClient(recall=error)
        bridge = MemoryHookBridge(client, _settings(fail_open=False))

        with pytest.raises(MemoryHookClientError, match="not_authorized"):
            await bridge.before_run(_context(), "输入")

    anyio.run(profile_id)


# ---------------------------------------------------------------------------
# Phase 1 已移除的 AfterRun/capture 相关测试：
# - test_before_and_after_run_execute_at_most_once_per_top_level_turn（AfterRun 半部分）
# - test_after_run_retries_same_event_and_can_fail_open
# - test_runner_does_not_capture_when_agent_fails
# AfterRun capture 由模型自主调用 capture_completed_turn MCP 工具，不再经 bridge。
# HookedAgentRunner / after_run_success / _capture 已删除。
# ---------------------------------------------------------------------------
