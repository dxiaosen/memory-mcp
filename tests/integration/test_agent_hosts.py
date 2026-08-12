from pathlib import Path

import anyio
import pytest
from memory_mcp_agent import (
    AgentHookAdapter,
    AgentHookInput,
    AgentHookInputError,
    AgentHookOutcome,
    AgentTurnEvent,
    MemoryHookBridge,
    MemoryHookClientError,
    MemoryHookSettings,
    RecallResponse,
    TurnStateStore,
    parse_hook_input,
    render_command_hook_output,
)
from memory_mcp_agent.cli import render_hook_output
from memory_mcp_agent.client import CaptureResponse, CaptureSummary, RecalledItem


def _settings() -> MemoryHookSettings:
    return MemoryHookSettings(
        mcp_url="http://127.0.0.1:8765/mcp",
        bearer_token="configured-token",
        _env_file=None,
    )


class _FakeClient:
    def __init__(
        self,
        *,
        with_memory: bool = True,
        recall_error: MemoryHookClientError | None = None,
        capture_error: MemoryHookClientError | None = None,
    ) -> None:
        self.with_memory = with_memory
        self.recall_error = recall_error
        self.capture_error = capture_error
        self.recall_calls: list[dict[str, object]] = []
        self.capture_calls: list[dict[str, object]] = []

    async def recall_memory(self, **arguments: object) -> RecallResponse:
        self.recall_calls.append(arguments)
        if self.recall_error is not None:
            raise self.recall_error
        items = (
            (
                RecalledItem(
                    memory_id="memory-1",
                    revision_id="revision-1",
                    content="项目周报默认使用表格",
                ),
            )
            if self.with_memory
            else ()
        )
        return RecallResponse(
            ok=True,
            request_id="request-recall",
            items=items,
            rendered_context=(
                "<memory-context>项目周报默认使用表格</memory-context>" if items else ""
            ),
            estimated_tokens=10 if items else 0,
            token_budget=600,
            truncated=False,
        )

    async def capture_completed_turn(self, **arguments: object) -> CaptureResponse:
        self.capture_calls.append(arguments)
        if self.capture_error is not None:
            raise self.capture_error
        return CaptureResponse(
            ok=True,
            request_id="request-capture",
            capture_id="capture-1",
            status="pending",
            replayed=False,
            summary=CaptureSummary(),
        )



def _adapter(
    client: _FakeClient,
    state: TurnStateStore,
) -> AgentHookAdapter:
    settings = _settings()
    return AgentHookAdapter(
        MemoryHookBridge(client, settings),
        settings,
        state,
    )


def _event(**payload: object) -> AgentTurnEvent:
    event = AgentHookInput.model_validate(payload).normalize()
    assert event is not None
    return event


@pytest.mark.parametrize(
    ("id_field", "state_directory"),
    [
        ("turn_id", "codex"),
        ("prompt_id", "claude-code"),
    ],
)
def test_supported_hosts_share_active_memory_flow(
    tmp_path: Path,
    id_field: str,
    state_directory: str,
) -> None:
    """BeforeRun 召回注入 + Stop 阶段入队 capture（服务端队列异步抽取）。"""

    async def profile_id() -> None:
        client = _FakeClient()
        state = TurnStateStore(tmp_path / state_directory / "hooks")
        identity = {id_field: "turn-1"}
        before = _event(
            session_id="session-1",
            cwd=str(tmp_path),
            hook_event_name="UserPromptSubmit",
            prompt="项目周报怎么写？",
            **identity,
        )
        before_outcome = await _adapter(client, state).handle(before)

        assert before.phase == "before_run"
        assert render_command_hook_output(before_outcome) == {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": (
                    "<memory-context>项目周报默认使用表格</memory-context>"
                ),
            }
        }
        assert len(client.recall_calls) == 1
        assert client.recall_calls[0]["profile_id"] is None

        after = _event(
            session_id="session-1",
            cwd=str(tmp_path),
            hook_event_name="Stop",
            last_assistant_message="已经按表格生成。",
            **identity,
        )
        after_outcome = await _adapter(client, state).handle(after)

        # Stop hook 入队 capture：返回 AgentHookOutcome()（无 warning），capture_calls=1
        assert after_outcome == AgentHookOutcome()
        assert len(client.capture_calls) == 1
        assert client.capture_calls[0]["conversation_id"] == "session-1"
        assert client.capture_calls[0]["turn_id"] == "turn-1"
        assert client.capture_calls[0]["user_input"] == "项目周报怎么写？"
        assert client.capture_calls[0]["final_output"] == "已经按表格生成。"

    anyio.run(profile_id)


# ---------------------------------------------------------------------------
# Stop hook 恢复强制入队 capture（经服务端队列异步抽取）。document provenance
# 暂退化（服务端组装 [user, assistant] 两条 messages，不解析 transcript 文件
# 来源）——已知债务，后续可补 transcript_path → document_messages。inspect/manage
# turn 跳过入队已恢复（_is_inspect_or_manage_turn）。
# ---------------------------------------------------------------------------


def test_after_run_enqueues_capture(tmp_path: Path) -> None:
    """Stop hook 入队 capture（毫秒级），即使携带 transcript_path 也正常入队。"""

    async def profile_id() -> None:
        import json

        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            json.dumps({"type": "user", "message": {"content": "x"}}) + "\n",
            encoding="utf-8",
        )
        client = _FakeClient()
        state = TurnStateStore(tmp_path / "hooks")
        adapter = _adapter(client, state)
        await adapter.handle(
            _event(
                session_id="session-1",
                cwd=str(tmp_path),
                hook_event_name="UserPromptSubmit",
                prompt_id="turn-1",
                prompt="任意业务问题",
            )
        )
        output = await adapter.handle(
            _event(
                session_id="session-1",
                cwd=str(tmp_path),
                hook_event_name="Stop",
                prompt_id="turn-1",
                last_assistant_message="任意最终回复。",
                transcript_path=str(transcript),
            )
        )

        # 入队成功 → 无 warning
        assert output == AgentHookOutcome()
        assert len(client.capture_calls) == 1

    anyio.run(profile_id)


def test_inspect_turn_with_memory_management_tool_skips_capture(
    tmp_path: Path,
) -> None:
    """当前轮次 assistant 调用 memory 管理工具时跳过 capture 入队。"""

    async def profile_id() -> None:
        import json

        # transcript 含一条 list_memories 工具调用（管理类）
        transcript = tmp_path / "transcript.jsonl"
        entries = [
            {"type": "user", "message": {"content": "列出所有记忆"}},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "list_memories",
                            "id": "tool-1",
                            "input": {},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": "[]",
                        }
                    ]
                },
            },
            {"type": "assistant", "message": {"content": "无记忆"}},
        ]
        transcript.write_text(
            "".join(json.dumps(e) + "\n" for e in entries),
            encoding="utf-8",
        )
        client = _FakeClient()
        state = TurnStateStore(tmp_path / "hooks")
        adapter = _adapter(client, state)
        await adapter.handle(
            _event(
                session_id="session-1",
                cwd=str(tmp_path),
                hook_event_name="UserPromptSubmit",
                prompt_id="turn-1",
                prompt="列出所有记忆",
            )
        )
        output = await adapter.handle(
            _event(
                session_id="session-1",
                cwd=str(tmp_path),
                hook_event_name="Stop",
                prompt_id="turn-1",
                last_assistant_message="无记忆",
                transcript_path=str(transcript),
            )
        )

        # inspect/manage turn → 跳过入队
        assert output.warning_code == "inspect_or_manage_turn"
        assert len(client.capture_calls) == 0

    anyio.run(profile_id)


def test_capture_transport_failure_fails_open(tmp_path: Path) -> None:
    """入队失败走 fail-open：warning_code 非空，不阻断 Agent。"""

    async def profile_id() -> None:
        client = _FakeClient(
            capture_error=MemoryHookClientError(
                "memory_mcp_unavailable",
                retryable=True,
            )
        )
        state = TurnStateStore(tmp_path / "hooks")
        adapter = _adapter(client, state)
        await adapter.handle(
            _event(
                session_id="session-1",
                cwd=str(tmp_path),
                hook_event_name="UserPromptSubmit",
                prompt_id="turn-1",
                prompt="业务问题",
            )
        )
        output = await adapter.handle(
            _event(
                session_id="session-1",
                cwd=str(tmp_path),
                hook_event_name="Stop",
                prompt_id="turn-1",
                last_assistant_message="业务回复。",
            )
        )

        assert output.warning_code == "capture_memory_mcp_unavailable"

    anyio.run(profile_id)


def test_no_memory_returns_strict_empty_json(tmp_path: Path) -> None:
    async def profile_id() -> None:
        client = _FakeClient(with_memory=False)
        output = await _adapter(
            client,
            TurnStateStore(tmp_path / "hooks"),
        ).handle(
            _event(
                session_id="session-1",
                turn_id="turn-1",
                cwd=str(tmp_path),
                hook_event_name="UserPromptSubmit",
                prompt="没有相关记忆",
            )
        )

        assert output == AgentHookOutcome()
        rendered = render_command_hook_output(output)
        assert rendered == {}
        assert render_hook_output(rendered) == "{}"

    anyio.run(profile_id)


# test_stop_without_saved_prompt_fails_open_without_capture 已删除：
# Phase 1 后 _before 不再保存 turn state，_after 为 no-op，
# "missing_turn_state" 跳过理由不再存在。Stop 无论有无前置 BeforeRun
# 均返回 AgentHookOutcome()。


def test_recall_failure_fails_open(tmp_path: Path) -> None:
    """BeforeRun 召回失败时 fail-open 返回 warning_code，不中断 Agent 任务。"""

    async def profile_id() -> None:
        client = _FakeClient(
            recall_error=MemoryHookClientError(
                "memory_mcp_unavailable",
                retryable=True,
            )
        )
        state = TurnStateStore(tmp_path / "hooks")
        output = await _adapter(client, state).handle(
            _event(
                session_id="session-1",
                turn_id="turn-1",
                cwd=str(tmp_path),
                hook_event_name="UserPromptSubmit",
                prompt="本轮仍应继续",
            )
        )

        assert output == AgentHookOutcome(
            warning_code="recall_memory_mcp_unavailable"
        )

    anyio.run(profile_id)


# ---------------------------------------------------------------------------
# Stop hook 恢复强制入队后，outbox 重投机制不再需要（入队毫秒级，失败走
# fail-open，下轮 Stop 幂等兜底）。以下原 outbox 重投测试不再适用：
# - test_capture_transport_failure_keeps_staged_payload：入队失败不入 outbox。
# - test_stop_without_final_output_keeps_saved_prompt：missing final_output
#   仍 skip（missing_final_output）。
# - test_reprocess_required_is_retried_by_later_stop：重投由服务端 worker 负责。
# ---------------------------------------------------------------------------


def test_unsupported_subagent_event_has_no_side_effect(tmp_path: Path) -> None:
    event = parse_hook_input(
        {
            "session_id": "session-1",
            "turn_id": "turn-1",
            "cwd": str(tmp_path),
            "hook_event_name": "SubagentStop",
            "last_assistant_message": "内部结果",
        }
    )

    assert event is None


def test_conflicting_or_missing_host_turn_identifiers_are_rejected() -> None:
    conflicting = AgentHookInput(
        session_id="session-1",
        turn_id="codex-turn",
        prompt_id="claude-prompt",
        cwd="/workspace",
        hook_event_name="UserPromptSubmit",
        prompt="问题",
    )
    missing = AgentHookInput(
        session_id="session-1",
        cwd="/workspace",
        hook_event_name="UserPromptSubmit",
        prompt="问题",
    )

    with pytest.raises(AgentHookInputError, match="conflicting_turn_identifiers"):
        conflicting.normalize()
    with pytest.raises(AgentHookInputError, match="missing_turn_identifier"):
        missing.normalize()


def test_canonical_agent_contract_uses_same_adapter_without_host_branch(
    tmp_path: Path,
) -> None:
    """通用合同 BeforeRun 召回注入 + AfterRun 入队 capture（服务端队列异步抽取）。"""

    async def profile_id() -> None:
        client = _FakeClient()
        state = TurnStateStore(tmp_path / "generic-agent" / "hooks")
        adapter = _adapter(client, state)

        before = parse_hook_input(
            {
                "conversation_id": "conversation-1",
                "run_id": "run-1",
                "cwd": str(tmp_path),
                "hook_event_name": "BeforeRun",
                "user_input": "请按项目约定生成周报",
            }
        )
        assert before is not None
        before_outcome = await adapter.handle(before)

        after = parse_hook_input(
            {
                "conversation_id": "conversation-1",
                "run_id": "run-1",
                "cwd": str(tmp_path),
                "hook_event_name": "AfterRun",
                "final_output": "已经生成周报。",
            }
        )
        assert after is not None
        after_outcome = await adapter.handle(after)

        assert before.phase == "before_run"
        assert before_outcome.additional_context is not None
        assert after.phase == "after_run"
        # Stop hook 入队 capture
        assert after_outcome == AgentHookOutcome()
        assert len(client.recall_calls) == 1
        assert len(client.capture_calls) == 1

    anyio.run(profile_id)


def test_equal_compatibility_aliases_are_accepted() -> None:
    event = parse_hook_input(
        {
            "session_id": "conversation-1",
            "conversation_id": "conversation-1",
            "turn_id": "run-1",
            "prompt_id": "run-1",
            "run_id": "run-1",
            "cwd": "/workspace",
            "hook_event_name": "BeforeRun",
            "prompt": "同一输入",
            "user_input": "同一输入",
        }
    )

    assert event == AgentTurnEvent(
        phase="before_run",
        conversation_id="conversation-1",
        turn_id="run-1",
        cwd="/workspace",
        user_input="同一输入",
    )
