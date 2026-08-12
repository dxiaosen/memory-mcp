"""Agent command Hook 输入、通用轮次事件与输出适配。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from memory_mcp_agent.bridge import MemoryHookBridge
from memory_mcp_agent.context import HookContext
from memory_mcp_agent.logging import log_event, stable_reference
from memory_mcp_agent.settings import MemoryHookSettings
from memory_mcp_agent.state import TurnState, TurnStateStore
from memory_mcp_agent.transcript import (
    collect_turn_tool_uses,
    extract_document_messages,
)

_LOGGER = logging.getLogger(__name__)
# 把各宿主的事件名归一化到两个通用阶段；不在表内的事件被忽略。
_EVENT_PHASES: dict[str, Literal["before_run", "after_run"]] = {
    "UserPromptSubmit": "before_run",
    "BeforeRun": "before_run",
    "Stop": "after_run",
    "AfterRun": "after_run",
}
# Memory MCP 管理类工具名。当前轮次 assistant 调用了其中任一即判定为
# inspect/manage turn，跳过 capture 入队——这类 turn 的内容是查看/管理已存储
# 记忆（列表/撤销/确认/关联等），不是可抽取的业务事实，白入队只会让 worker
# 白烧一次抽取。recall_memory 不在此列：BeforeRun hook 每个业务 turn 都自动调
# 它，若计入会把所有业务 turn 误判为 inspect。capture_completed_turn 是 hook
# 自身入队通道，也不在此列。
_MEMORY_MANAGEMENT_TOOLS: frozenset[str] = frozenset(
    {
        "search_memories",
        "list_memories",
        "get_memory",
        "get_memory_stats",
        "revoke_memory",
        "link_memories",
        "revoke_memory_relation",
        "list_pending_reviews",
        "confirm_pending_memory",
        "reject_pending_memory",
        "batch_confirm_pending",
    }
)


class AgentHookInputError(ValueError):
    """宿主 Hook 输入缺少稳定顶层轮次语义。"""


class AgentTurnEvent(BaseModel):
    """主动记忆内部使用的宿主无关顶层轮次事件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    phase: Literal["before_run", "after_run"]
    conversation_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    cwd: str = Field(min_length=1)
    user_input: str | None = None
    final_output: str | None = None
    transcript_path: str | None = None

    def required_user_input(self) -> str:
        """返回 Before 阶段的非空用户输入。"""

        if self.user_input is None or not self.user_input.strip():
            raise AgentHookInputError("missing_user_input")
        return self.user_input


class AgentHookOutcome(BaseModel):
    """主动记忆执行结果，不包含任何宿主专用 JSON。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    additional_context: str | None = None
    warning_code: str | None = None


class AgentHookInput(BaseModel):
    """command Hook 的兼容输入边界。

    Codex、Claude Code 与通用字段只在这里归一化；后续执行层不识别宿主。
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    hook_event_name: str = Field(min_length=1)
    cwd: str = Field(min_length=1)

    # 会话标识：Codex/Claude Code 用 session_id，通用合同用 conversation_id，二者取一。
    session_id: str | None = Field(default=None, min_length=1)
    conversation_id: str | None = Field(default=None, min_length=1)

    # 轮次标识：run_id（通用）、turn_id（Codex）、prompt_id（Claude Code）三选一。
    turn_id: str | None = Field(default=None, min_length=1)
    prompt_id: str | None = Field(default=None, min_length=1)
    run_id: str | None = Field(default=None, min_length=1)

    # 内容字段：prompt（Codex/Claude Code）与 user_input（通用合同）取一；
    # last_assistant_message（Codex/Claude Code）与 final_output（通用合同）取一。
    prompt: str | None = None
    user_input: str | None = None
    last_assistant_message: str | None = None
    final_output: str | None = None

    # 某些宿主在 Stop 再入时提供该字段；主动记忆不据此改变业务行为。
    stop_hook_active: bool = False

    # Claude Code 在 Stop / UserPromptSubmit 事件提供 transcript_path：指向
    # 会话 JSONL 记录。Host Adapter 据此补全文件/工具来源 provenance
    # ，Core/Server 不感知 Claude Code 格式。
    transcript_path: str | None = Field(default=None, min_length=1)

    @property
    def supported(self) -> bool:
        """只对顶层轮次开始和结束事件启用主动记忆。"""

        return self.hook_event_name in _EVENT_PHASES

    def normalize(self) -> AgentTurnEvent | None:
        """把兼容输入转换为唯一的通用轮次事件。"""

        phase = _EVENT_PHASES.get(self.hook_event_name)
        if phase is None:
            return None

        conversation_id = _one_value(
            (self.conversation_id, self.session_id),
            missing_code="missing_conversation_identifier",
            conflict_code="conflicting_conversation_identifiers",
        )
        turn_id = _one_value(
            (self.run_id, self.turn_id, self.prompt_id),
            missing_code="missing_turn_identifier",
            conflict_code="conflicting_turn_identifiers",
        )
        user_input = _optional_one_value(
            (self.user_input, self.prompt),
            conflict_code="conflicting_user_inputs",
        )
        final_output = _optional_one_value(
            (self.final_output, self.last_assistant_message),
            conflict_code="conflicting_final_outputs",
        )

        return AgentTurnEvent(
            phase=phase,
            conversation_id=conversation_id,
            turn_id=turn_id,
            cwd=self.cwd,
            user_input=user_input,
            final_output=final_output,
            transcript_path=self.transcript_path,
        )


HookOutput = dict[str, Any]


def render_command_hook_output(outcome: AgentHookOutcome) -> HookOutput:
    """把通用结果渲染为 Codex/Claude command Hook 的公共 JSON。"""

    if outcome.warning_code is not None:
        return {"systemMessage": f"Memory MCP Hook: {outcome.warning_code}"}
    if outcome.additional_context is not None:
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": outcome.additional_context,
            }
        }
    return {}


class AgentHookAdapter:
    """在通用顶层轮次事件和 Memory Hook Bridge 之间接线。

    BeforeRun 写本地 prompt 暂存（Stop 事件不带 user_input）并召回注入；
    AfterRun 经 Stop hook 强制入队 capture（服务端队列异步抽取）。入队毫秒级
    返回，失败走 fail-open——下一轮 Stop 用同 conversation_id+turn_id 再入队
    时服务端 event_id 幂等兜底。inspect/manage turn（查看/操作已存记忆）跳过
    入队避免白烧 worker 抽取。
    """

    def __init__(
        self,
        bridge: MemoryHookBridge,
        settings: MemoryHookSettings,
        state: TurnStateStore,
    ) -> None:
        self._bridge = bridge
        self._settings = settings
        self._state = state

    async def handle(self, event: AgentTurnEvent) -> AgentHookOutcome:
        """处理一个已规范化的顶层轮次事件。"""

        run_reference = stable_reference(f"{event.conversation_id}\x1f{event.turn_id}")
        log_event(
            _LOGGER,
            logging.INFO,
            "agent_hook.started",
            phase=event.phase,
            run_reference=run_reference,
        )
        self._state.cleanup_expired()
        if event.phase == "before_run":
            return await self._before(event, run_reference)
        return await self._after(event, run_reference)

    async def _before(
        self,
        event: AgentTurnEvent,
        run_reference: str,
    ) -> AgentHookOutcome:
        user_input = event.required_user_input()
        self._state.save(
            TurnState(
                session_id=event.conversation_id,
                turn_id=event.turn_id,
                prompt=user_input,
                profile_id=self._settings.profile_id,
            )
        )
        result = await self._bridge.before_run(
            self._context(event),
            user_input,
        )
        log_event(
            _LOGGER,
            logging.INFO,
            "agent_hook.recall.completed",
            recalled_count=result.recalled_count,
            run_reference=run_reference,
            truncated=result.truncated,
            warning_code=result.warning_code,
        )
        if result.warning_code is not None:
            return AgentHookOutcome(warning_code=f"recall_{result.warning_code}")
        return AgentHookOutcome(additional_context=result.memory_context)

    async def _after(
        self,
        event: AgentTurnEvent,
        run_reference: str,
    ) -> AgentHookOutcome:
        if event.final_output is None or not event.final_output.strip():
            return self._skip_after(
                "missing_final_output",
                run_reference=run_reference,
            )

        saved = self._state.load(event.conversation_id, event.turn_id)
        if saved is None:
            return self._skip_after(
                "missing_turn_state",
                run_reference=run_reference,
            )

        if _is_inspect_or_manage_turn(
            event.transcript_path,
            cwd=event.cwd,
            user_prompt=saved.prompt,
        ):
            self._state.delete(event.conversation_id, event.turn_id)
            return self._skip_after(
                "inspect_or_manage_turn",
                run_reference=run_reference,
            )

        document_messages = extract_document_messages(
            event.transcript_path,
            cwd=event.cwd,
            user_prompt=saved.prompt,
        )
        result = await self._bridge.after_run_success(
            self._context(event),
            user_input=saved.prompt,
            final_output=event.final_output,
            document_messages=document_messages,
        )
        # 入队成功（pending/completed/failed/reprocess_required）即删本地暂存；
        # 入队失败已走 fail-open，warning_code 非空时也删（下轮幂等兜底）。
        self._state.delete(event.conversation_id, event.turn_id)
        if result.warning_code is not None:
            return AgentHookOutcome(warning_code=f"capture_{result.warning_code}")
        return AgentHookOutcome()

    def _context(self, event: AgentTurnEvent) -> HookContext:
        return HookContext(
            conversation_id=event.conversation_id,
            turn_id=event.turn_id,
            profile_id=self._settings.profile_id,
        )

    @staticmethod
    def _skip_after(
        code: str,
        *,
        run_reference: str,
    ) -> AgentHookOutcome:
        log_event(
            _LOGGER,
            logging.WARNING,
            "agent_hook.capture.skipped",
            reason_code=code,
            run_reference=run_reference,
        )
        return AgentHookOutcome(warning_code=code)


def parse_hook_input(value: Mapping[str, Any]) -> AgentTurnEvent | None:
    """解析 command Hook JSON，并返回通用事件或忽略结果。"""

    return AgentHookInput.model_validate(value).normalize()


def _one_value(
    values: tuple[str | None, ...],
    *,
    missing_code: str,
    conflict_code: str,
) -> str:
    """从多个候选字段中取唯一非空值；全空报 missing，多于一个报 conflict。"""

    value = _optional_one_value(values, conflict_code=conflict_code)
    if value is None or not value.strip():
        raise AgentHookInputError(missing_code)
    return value


def _optional_one_value(
    values: tuple[str | None, ...],
    *,
    conflict_code: str,
) -> str | None:
    """与 _one_value 同样的归一化，但允许全部缺失（返回 None）。"""

    present = {value for value in values if value is not None}
    if len(present) > 1:
        raise AgentHookInputError(conflict_code)
    return next(iter(present), None)


def _is_inspect_or_manage_turn(
    transcript_path: str | None,
    cwd: str,
    user_prompt: str,
) -> bool:
    """判断当前轮次是否为查看/管理已存储记忆的 inspect/manage turn。

    基于结构性信号：当前轮次 assistant 是否调用了 memory 管理类工具
    （search_memories/list_memories/revoke_memory 等，不含 recall_memory）。
    inspect/manage turn 的 assistant 必然调用其中至少一个来查看或操作记忆；
    业务 turn 的 assistant 只依赖 BeforeRun hook 自动调的 recall_memory，不调这些。

    无 transcript_path（通用合同 / 非 Claude Code 宿主）或解析失败时返回 False，
    降级为不跳过——保持现有 capture 行为，宁可白入队一次也不误伤业务 turn。
    """

    if not transcript_path:
        return False
    tool_names = collect_turn_tool_uses(
        transcript_path,
        cwd=cwd,
        user_prompt=user_prompt,
    )
    return bool(tool_names & _MEMORY_MANAGEMENT_TOOLS)
