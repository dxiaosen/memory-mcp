from pathlib import Path
from typing import Literal

import anyio
import pytest
from memory_mcp_agent import (
    AgentHookAdapter,
    AgentHookInput,
    AgentHookInputError,
    AgentHookOutcome,
    AgentTurnEvent,
    CaptureResponse,
    MemoryHookBridge,
    MemoryHookClientError,
    MemoryHookSettings,
    RecallResponse,
    TurnStateStore,
    parse_hook_input,
    render_command_hook_output,
)
from memory_mcp_agent.cli import render_hook_output
from memory_mcp_agent.client import CaptureSummary, RecalledItem


def _settings() -> MemoryHookSettings:
    return MemoryHookSettings(
        mcp_url="http://127.0.0.1:8765/mcp",
        bearer_token="configured-token",
        capture_retry_delay_seconds=0,
        _env_file=None,
    )


class _FakeClient:
    def __init__(
        self,
        *,
        with_memory: bool = True,
        recall_error: MemoryHookClientError | None = None,
        capture_error: MemoryHookClientError | None = None,
        capture_status: Literal[
            "completed", "failed", "reprocess_required"
        ] = "completed",
        capture_failure_code: str | None = None,
    ) -> None:
        self.with_memory = with_memory
        self.recall_error = recall_error
        self.capture_error = capture_error
        self.capture_status = capture_status
        self.capture_failure_code = capture_failure_code
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

    async def capture_completed_turn(
        self,
        **arguments: object,
    ) -> CaptureResponse:
        self.capture_calls.append(arguments)
        if self.capture_error is not None:
            raise self.capture_error
        return CaptureResponse(
            ok=True,
            request_id="request-capture",
            capture_id="capture-1",
            status=self.capture_status,
            replayed=False,
            summary=CaptureSummary(auto_saved_count=1),
            created_memory_ids=("memory-1",),
            failure_code=self.capture_failure_code,
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
        assert state.load("session-1", "turn-1") is not None

        after = _event(
            session_id="session-1",
            cwd=str(tmp_path),
            hook_event_name="Stop",
            last_assistant_message="已经按表格生成。",
            **identity,
        )
        after_outcome = await _adapter(client, state).handle(after)

        assert after_outcome == AgentHookOutcome()
        assert state.load("session-1", "turn-1") is None
        assert len(client.recall_calls) == 1
        assert len(client.capture_calls) == 1
        capture = client.capture_calls[0]
        assert capture["conversation_id"] == "session-1"
        assert capture["turn_id"] == "turn-1"
        assert capture["user_input"] == "项目周报怎么写？"
        assert capture["final_output"] == "已经按表格生成。"
        assert capture["profile_id"] is None
        assert client.recall_calls[0]["profile_id"] is None

    anyio.run(profile_id)


def test_transcript_path_surfaces_document_messages_in_capture(
    tmp_path: Path,
) -> None:
    """Stop 事件携带 transcript_path 时，文档来源消息应随 capture 请求送达。

    相关：Claude Code Stop hook 提供 transcript_path，Host Adapter
    解析出文件读取来源，构造 role=tool/source_type=document 的消息，使候选
    Evidence provenance 能映射到真实文档而非一律归到 assistant conversation。
    """

    async def profile_id() -> None:
        import json

        materials_dir = tmp_path / "materials"
        materials_dir.mkdir()
        file_path = str(materials_dir / "04_纪要.md")
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            "\n".join(
                json.dumps(entry)
                for entry in (
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "call-1",
                                    "name": "Read",
                                    "input": {"file_path": file_path},
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
                                    "tool_use_id": "call-1",
                                    "content": "收入同比增长 35%",
                                }
                            ]
                        },
                    },
                )
            )
            + "\n",
            encoding="utf-8",
        )
        client = _FakeClient()
        state = TurnStateStore(tmp_path / "claude-code" / "hooks")
        before = _event(
            session_id="session-1",
            cwd=str(tmp_path),
            hook_event_name="UserPromptSubmit",
            prompt_id="turn-1",
            prompt="04 纪要里收入怎么样？",
        )
        await _adapter(client, state).handle(before)

        after = _event(
            session_id="session-1",
            cwd=str(tmp_path),
            hook_event_name="Stop",
            prompt_id="turn-1",
            last_assistant_message="收入同比增长 35%。",
            transcript_path=str(transcript),
        )
        await _adapter(client, state).handle(after)

        assert len(client.capture_calls) == 1
        capture = client.capture_calls[0]
        documents = capture["document_messages"]
        assert len(documents) == 1
        doc = documents[0]
        assert doc["source_type"] == "document"
        assert doc["source_uri"] == "materials/04_纪要.md"
        assert doc["source_title"] == "04_纪要.md"
        assert doc["tool_name"] == "Read"
        assert doc["content"] == "收入同比增长 35%"

    anyio.run(profile_id)


def test_second_turn_excludes_first_turn_tool_messages(tmp_path: Path) -> None:
    """两轮 E2E：第二轮不应重复包含第一轮 tool/document 消息。

    第一轮读文件（transcript 含 Read 工具调用），第二轮仅用户判断 + 回复（无工具调用）。
    第二轮 Stop 时 transcript 已含两轮记录，但 capture 只应发送当前轮次--document_messages
    为空（等价 message_count=2），不重复第一轮的文档。
    """

    async def profile_id() -> None:
        import json

        def _write_transcript(path: Path, entries: list[dict]) -> None:
            path.write_text(
                "\n".join(json.dumps(entry) for entry in entries) + "\n",
                encoding="utf-8",
            )

        materials_dir = tmp_path / "materials"
        materials_dir.mkdir()
        file_path = str(materials_dir / "04_纪要.md")
        transcript = tmp_path / "transcript.jsonl"

        def _read(call_id: str, content: str) -> list[dict]:
            return [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": call_id,
                                "name": "Read",
                                "input": {"file_path": file_path},
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
                                "tool_use_id": call_id,
                                "content": content,
                            }
                        ]
                    },
                },
            ]

        client = _FakeClient()
        state = TurnStateStore(tmp_path / "claude-code" / "hooks")
        adapter = _adapter(client, state)

        # 第一轮：读文件。
        await adapter.handle(
            _event(
                session_id="session-1",
                cwd=str(tmp_path),
                hook_event_name="UserPromptSubmit",
                prompt_id="turn-1",
                prompt="04 纪要里收入怎么样？",
            )
        )
        _write_transcript(
            transcript,
            [
                {"type": "user", "message": {"content": "04 纪要里收入怎么样？"}},
                *_read("call-1", "收入同比增长 35%"),
                {"type": "assistant", "message": {"content": "收入同比增长 35%。"}},
            ],
        )
        await adapter.handle(
            _event(
                session_id="session-1",
                cwd=str(tmp_path),
                hook_event_name="Stop",
                prompt_id="turn-1",
                last_assistant_message="收入同比增长 35%。",
                transcript_path=str(transcript),
            )
        )
        first_capture = client.capture_calls[0]
        assert len(first_capture["document_messages"]) == 1
        assert first_capture["document_messages"][0]["source_uri"] == (
            "materials/04_纪要.md"
        )

        # 第二轮：仅用户长期研究判断，无工具调用；transcript 已累积两轮。
        await adapter.handle(
            _event(
                session_id="session-1",
                cwd=str(tmp_path),
                hook_event_name="UserPromptSubmit",
                prompt_id="turn-2",
                prompt="这是我的长期研究判断，AI 热管理材料收入占比提升会推动利润结构改善。",
            )
        )
        _write_transcript(
            transcript,
            [
                {"type": "user", "message": {"content": "04 纪要里收入怎么样？"}},
                *_read("call-1", "收入同比增长 35%"),
                {"type": "assistant", "message": {"content": "收入同比增长 35%。"}},
                {
                    "type": "user",
                    "message": {
                        "content": (
                            "这是我的长期研究判断，AI 热管理材料收入占比提升会推动利润结构改善。"
                        )
                    },
                },
                {"type": "assistant", "message": {"content": "已记录你的研究判断。"}},
            ],
        )
        await adapter.handle(
            _event(
                session_id="session-1",
                cwd=str(tmp_path),
                hook_event_name="Stop",
                prompt_id="turn-2",
                last_assistant_message="已记录你的研究判断。",
                transcript_path=str(transcript),
            )
        )

        second_capture = client.capture_calls[1]
        # 第二轮无工具调用 -> 不重复第一轮文档，document_messages 为空。
        assert second_capture["document_messages"] == []
        assert second_capture["turn_id"] == "turn-2"
        assert (
            second_capture["user_input"]
            == "这是我的长期研究判断，AI 热管理材料收入占比提升会推动利润结构改善。"
        )

    anyio.run(profile_id)


def test_inspect_turn_with_memory_management_tool_skips_capture(
    tmp_path: Path,
) -> None:
    """查看/管理记忆的 inspect turn（assistant 调用了 memory 管理工具）应跳过 capture。

    相关：inspect/manage turn 的内容是查看/管理已存储记忆，不是可抽取的业务事实。
    强抽只会白烧模型调用且因 user 原文不含 source_expression 片段而必失败。
    信号：当前轮次 transcript 含 memory 管理类工具调用（不含 recall_memory）。
    """
    import json

    async def profile_id() -> None:
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            "\n".join(
                json.dumps(entry)
                for entry in (
                    {
                        "type": "user",
                        "message": {"content": "请显式查看我的青禾食品记忆。"},
                    },
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "call-search",
                                    "name": "search_memories",
                                    "input": {"query": "青禾食品"},
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
                                    "tool_use_id": "call-search",
                                    "content": "5 条记忆",
                                }
                            ]
                        },
                    },
                    {"type": "assistant", "message": {"content": "共 5 条记忆。"}},
                )
            )
            + "\n",
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
                prompt="请显式查看我的青禾食品记忆。",
            )
        )
        output = await adapter.handle(
            _event(
                session_id="session-1",
                cwd=str(tmp_path),
                hook_event_name="Stop",
                prompt_id="turn-1",
                last_assistant_message="共 5 条记忆。",
                transcript_path=str(transcript),
            )
        )

        assert output == AgentHookOutcome(warning_code="inspect_or_manage_turn")
        assert client.capture_calls == []

    anyio.run(profile_id)


def test_business_turn_with_only_recall_memory_still_captures(
    tmp_path: Path,
) -> None:
    """业务 turn（assistant 未调 memory 管理工具）不应被误跳过。

    recall_memory 由 BeforeRun hook 自动调用，不计入 inspect/manage 信号；
    其他非 memory 工具（Read/Bash 等）也不计。这类 turn 必须正常 capture。
    """
    import json

    async def profile_id() -> None:
        materials_dir = tmp_path / "materials"
        materials_dir.mkdir()
        file_path = str(materials_dir / "纪要.md")
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            "\n".join(
                json.dumps(entry)
                for entry in (
                    {
                        "type": "user",
                        "message": {"content": "纪要里收入怎么样？"},
                    },
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "call-read",
                                    "name": "Read",
                                    "input": {"file_path": file_path},
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
                                    "tool_use_id": "call-read",
                                    "content": "收入同比增长 35%",
                                }
                            ]
                        },
                    },
                    {"type": "assistant", "message": {"content": "收入增长 35%。"}},
                )
            )
            + "\n",
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
                prompt="纪要里收入怎么样？",
            )
        )
        output = await adapter.handle(
            _event(
                session_id="session-1",
                cwd=str(tmp_path),
                hook_event_name="Stop",
                prompt_id="turn-1",
                last_assistant_message="收入增长 35%。",
                transcript_path=str(transcript),
            )
        )

        assert output == AgentHookOutcome()
        assert len(client.capture_calls) == 1

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


def test_stop_without_saved_prompt_fails_open_without_capture(
    tmp_path: Path,
) -> None:
    async def profile_id() -> None:
        client = _FakeClient()
        output = await _adapter(
            client,
            TurnStateStore(tmp_path / "hooks"),
        ).handle(
            _event(
                session_id="session-1",
                prompt_id="prompt-1",
                cwd=str(tmp_path),
                hook_event_name="Stop",
                last_assistant_message="本轮已经结束。",
            )
        )

        assert output == AgentHookOutcome(warning_code="missing_turn_state")
        assert client.capture_calls == []

    anyio.run(profile_id)


def test_recall_failure_keeps_state_and_fails_open(tmp_path: Path) -> None:
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

        assert output == AgentHookOutcome(warning_code="recall_memory_mcp_unavailable")
        assert state.load("session-1", "turn-1") is not None

    anyio.run(profile_id)


def test_capture_transport_failure_keeps_staged_payload(tmp_path: Path) -> None:
    async def profile_id() -> None:
        client = _FakeClient(capture_error=MemoryHookClientError("temporary_failure"))
        state = TurnStateStore(tmp_path / "hooks")
        await _adapter(client, state).handle(
            _event(
                session_id="session-1",
                prompt_id="prompt-1",
                cwd=str(tmp_path),
                hook_event_name="UserPromptSubmit",
                prompt="需要捕获的内容",
            )
        )

        output = await _adapter(client, state).handle(
            _event(
                session_id="session-1",
                prompt_id="prompt-1",
                cwd=str(tmp_path),
                hook_event_name="Stop",
                last_assistant_message="最终回复",
            )
        )

        assert output == AgentHookOutcome(warning_code="capture_temporary_failure")
        pending = state.load("session-1", "prompt-1")
        assert pending is not None
        assert pending.final_output == "最终回复"
        assert pending.capture_observed_at is not None
        assert len(client.capture_calls) == 1

    anyio.run(profile_id)


def test_stop_without_final_output_keeps_saved_prompt(tmp_path: Path) -> None:
    async def profile_id() -> None:
        client = _FakeClient()
        state = TurnStateStore(tmp_path / "hooks")
        adapter = _adapter(client, state)
        await adapter.handle(
            _event(
                session_id="session-1",
                turn_id="turn-1",
                cwd=str(tmp_path),
                hook_event_name="UserPromptSubmit",
                prompt="一个问题",
            )
        )

        output = await adapter.handle(
            _event(
                session_id="session-1",
                turn_id="turn-1",
                cwd=str(tmp_path),
                hook_event_name="Stop",
                last_assistant_message=None,
            )
        )

        assert output == AgentHookOutcome(warning_code="missing_final_output")
        assert state.load("session-1", "turn-1") is not None
        assert client.capture_calls == []

    anyio.run(profile_id)


def test_reprocess_required_is_retried_by_later_stop(tmp_path: Path) -> None:
    async def profile_id() -> None:
        state = TurnStateStore(tmp_path / "hooks")
        first_client = _FakeClient(
            capture_status="reprocess_required",
            capture_failure_code="extraction_unavailable",
        )
        first_adapter = _adapter(first_client, state)
        await first_adapter.handle(
            _event(
                session_id="session-1",
                turn_id="turn-1",
                cwd=str(tmp_path),
                hook_event_name="UserPromptSubmit",
                prompt="第一轮问题",
            )
        )
        first_outcome = await first_adapter.handle(
            _event(
                session_id="session-1",
                turn_id="turn-1",
                cwd=str(tmp_path),
                hook_event_name="Stop",
                last_assistant_message="第一轮最终回复",
            )
        )
        pending = state.load("session-1", "turn-1")

        assert first_outcome == AgentHookOutcome(
            warning_code="capture_extraction_unavailable"
        )
        assert pending is not None
        assert pending.capture_observed_at is not None

        # command Hook 每次是独立进程；新适配器会重投一个旧 payload，再处理当前轮次。
        second_client = _FakeClient()
        second_adapter = _adapter(second_client, state)
        await second_adapter.handle(
            _event(
                session_id="session-1",
                turn_id="turn-2",
                cwd=str(tmp_path),
                hook_event_name="UserPromptSubmit",
                prompt="第二轮问题",
            )
        )
        second_outcome = await second_adapter.handle(
            _event(
                session_id="session-1",
                turn_id="turn-2",
                cwd=str(tmp_path),
                hook_event_name="Stop",
                last_assistant_message="第二轮最终回复",
            )
        )

        assert second_outcome == AgentHookOutcome()
        assert len(second_client.capture_calls) == 2
        retry, current = second_client.capture_calls
        assert retry["turn_id"] == "turn-1"
        assert retry["observed_at"] == pending.capture_observed_at
        assert current["turn_id"] == "turn-2"
        assert state.load("session-1", "turn-1") is None
        assert state.load("session-1", "turn-2") is None

    anyio.run(profile_id)


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
        assert after_outcome == AgentHookOutcome()
        assert len(client.capture_calls) == 1
        capture = client.capture_calls[0]
        assert capture["conversation_id"] == "conversation-1"
        assert capture["turn_id"] == "run-1"
        assert capture["profile_id"] is None
        assert capture["user_input"] == "请按项目约定生成周报"
        assert capture["final_output"] == "已经生成周报。"

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
