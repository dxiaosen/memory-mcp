import asyncio
from datetime import UTC, datetime

import anyio
import pytest
from memory_mcp_agent import (
    CaptureResponse,
    HookContext,
    HookedAgentRunner,
    MemoryHookBridge,
    MemoryHookClientError,
    MemoryHookRunConflictError,
    MemoryHookSettings,
    RecallResponse,
)
from memory_mcp_agent.client import CaptureSummary, RecalledItem


def _settings(**overrides: object) -> MemoryHookSettings:
    values: dict[str, object] = {
        "mcp_url": "http://127.0.0.1:8765/mcp",
        "bearer_token": "configured-token",
        "capture_retry_delay_seconds": 0,
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


def _capture_response(*, replayed: bool = False) -> CaptureResponse:
    return CaptureResponse(
        ok=True,
        request_id="request-2",
        capture_id="capture-1",
        status="completed",
        replayed=replayed,
        summary=CaptureSummary(auto_saved_count=1),
        created_memory_ids=("memory-1",),
    )


class _FakeClient:
    def __init__(
        self,
        *,
        recall: RecallResponse | Exception | None = None,
        capture_failures: int = 0,
    ) -> None:
        self.recall = recall or _recall_response()
        self.capture_failures = capture_failures
        self.recall_calls = 0
        self.capture_calls: list[dict[str, object]] = []

    async def recall_memory(self, **_: object) -> RecallResponse:
        self.recall_calls += 1
        await asyncio.sleep(0)
        if isinstance(self.recall, Exception):
            raise self.recall
        return self.recall

    async def capture_completed_turn(
        self,
        **arguments: object,
    ) -> CaptureResponse:
        self.capture_calls.append(arguments)
        if len(self.capture_calls) <= self.capture_failures:
            raise MemoryHookClientError("temporary_failure", retryable=True)
        return _capture_response()


def _context() -> HookContext:
    return HookContext(
        conversation_id="conversation-1",
        turn_id="turn-1",
        subject="weekly-report",
    )


def test_before_and_after_run_execute_at_most_once_per_top_level_turn() -> None:
    async def profile_id() -> None:
        client = _FakeClient()
        bridge = MemoryHookBridge(client, _settings())
        before = await asyncio.gather(
            bridge.before_run(_context(), "项目周报"),
            bridge.before_run(_context(), "项目周报"),
        )
        observed_at = datetime(2026, 7, 30, tzinfo=UTC)
        after = await asyncio.gather(
            bridge.after_run_success(
                _context(),
                user_input="以后项目周报默认用表格",
                final_output="好的",
                observed_at=observed_at,
            ),
            bridge.after_run_success(
                _context(),
                user_input="以后项目周报默认用表格",
                final_output="好的",
                observed_at=observed_at,
            ),
        )

        assert before[0] == before[1]
        assert after[0] == after[1]
        assert client.recall_calls == 1
        assert len(client.capture_calls) == 1
        assert after[0].summary == CaptureSummary(auto_saved_count=1)

    anyio.run(profile_id)


def test_same_run_key_with_different_payload_is_rejected() -> None:
    async def profile_id() -> None:
        bridge = MemoryHookBridge(_FakeClient(), _settings())
        await bridge.before_run(_context(), "项目周报")
        with pytest.raises(MemoryHookRunConflictError, match="BeforeRun"):
            await bridge.before_run(_context(), "另一个请求")

        await bridge.after_run_success(
            _context(),
            user_input="输入",
            final_output="输出",
        )
        with pytest.raises(MemoryHookRunConflictError, match="AfterRun"):
            await bridge.after_run_success(
                _context(),
                user_input="输入",
                final_output="另一个输出",
            )

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


def test_after_run_retries_same_event_and_can_fail_open() -> None:
    async def retry_profile() -> None:
        client = _FakeClient(capture_failures=2)
        result = await MemoryHookBridge(
            client,
            _settings(capture_max_attempts=3),
        ).after_run_success(
            _context(),
            user_input="以后项目周报默认用表格",
            final_output="好的",
        )

        assert result.attempts == 3
        assert result.status == "completed"
        assert len({call["event_id"] for call in client.capture_calls}) == 1

    async def fail_open_profile() -> None:
        client = _FakeClient(capture_failures=5)
        result = await MemoryHookBridge(
            client,
            _settings(capture_max_attempts=2, fail_open=True),
        ).after_run_success(
            _context(),
            user_input="输入",
            final_output="输出",
        )

        assert result.status == "warning"
        assert result.warning_code == "temporary_failure"
        assert result.attempts == 2

    anyio.run(retry_profile)
    anyio.run(fail_open_profile)


def test_runner_does_not_capture_when_agent_fails() -> None:
    async def profile_id() -> None:
        client = _FakeClient()
        bridge = MemoryHookBridge(client, _settings())

        async def failing_agent(_: str, __: str | None) -> str:
            raise RuntimeError("agent failed")

        with pytest.raises(RuntimeError, match="agent failed"):
            await HookedAgentRunner(bridge, failing_agent).run(
                _context(),
                "输入",
            )
        assert client.recall_calls == 1
        assert client.capture_calls == []

    anyio.run(profile_id)


def test_fail_closed_re_raises_hook_error() -> None:
    async def profile_id() -> None:
        error = MemoryHookClientError("not_authorized")
        client = _FakeClient(recall=error)
        bridge = MemoryHookBridge(client, _settings(fail_open=False))

        with pytest.raises(MemoryHookClientError, match="not_authorized"):
            await bridge.before_run(_context(), "输入")

    anyio.run(profile_id)
