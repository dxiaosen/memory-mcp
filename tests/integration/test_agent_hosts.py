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
from memory_mcp_agent.client import RecalledItem


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
    ) -> None:
        self.with_memory = with_memory
        self.recall_error = recall_error
        self.recall_calls: list[dict[str, object]] = []

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
    """BeforeRun 召回注入 + Stop 阶段 no-op（Phase 1 后 capture 由模型自主调用）。"""

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

        # Phase 1: AfterRun is no-op; capture is model-driven, not hook-driven.
        assert after_outcome == AgentHookOutcome()
        assert len(client.recall_calls) == 1

    anyio.run(profile_id)


# ---------------------------------------------------------------------------
# Phase 1 已知债务：document provenance（transcript_path → document_messages）
# 与 inspect/manage turn 跳过 capture 的机制随 Stop hook capture 路径一并移除。
# 以下原 test_transcript_path_surfaces_document_messages_in_capture /
# test_second_turn_excludes_first_turn_tool_messages /
# test_inspect_turn_with_memory_management_tool_skips_capture /
# test_business_turn_with_only_recall_memory_still_captures 均已删除，
# 因为其测试的机制（Stop hook 解析 transcript 并调用 capture）已不存在。
# AfterRun 阶段现为 no-op，capture 完全由模型自主调用 capture_completed_turn
# MCP 工具触发。document provenance 需在后续 Phase 由模型侧或 server 侧补全。
# ---------------------------------------------------------------------------


def test_after_run_is_noop_regardless_of_transcript(tmp_path: Path) -> None:
    """Phase 1: AfterRun no-op 即使携带 transcript_path 也不触发 capture。"""

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

        assert output == AgentHookOutcome()
        # AfterRun no-op: 仅 BeforeRun 召回一次，无 capture 调用。

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
    """BeforeRun 召回失败时 fail-open 返回 warning_code，不中断 Agent 任务。

    Phase 1 后 _before 不再保存 turn state（outbox 已移除），故不再断言
    state 文件存在；召回失败 → warning 的路径仍然保留。
    """

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
# 以下 outbox 重试机制相关测试已在 Phase 1 移除：
# - test_capture_transport_failure_keeps_staged_payload：capture 失败时 outbox
#   保留 payload 待下次 Stop 重投。Phase 1 后 hook Stop 不再触发 capture，
#   outbox 不再写入，此机制不存在。
# - test_stop_without_final_output_keeps_saved_prompt：missing final_output 时
#   跳过 capture 并保留 state。Phase 1 后 Stop 为 no-op，不受 final_output 影响。
# - test_reprocess_required_is_retried_by_later_stop：reprocess_required 状态的
#   outbox 重投。同上，outbox 机制已移除。
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
    """通用合同 BeforeRun 召回注入 + AfterRun no-op（Phase 1 后 capture 模型自主）。"""

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
        # Phase 1: AfterRun is no-op; capture is model-driven, not hook-driven.
        assert after_outcome == AgentHookOutcome()
        assert len(client.recall_calls) == 1

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
