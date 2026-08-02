"""Agent command Hook 输入、通用轮次事件与输出适配。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from memory_mcp_agent.bridge import AfterRunResult, MemoryHookBridge
from memory_mcp_agent.context import HookContext
from memory_mcp_agent.logging import log_event, stable_reference
from memory_mcp_agent.settings import MemoryHookSettings
from memory_mcp_agent.state import TurnState, TurnStateError, TurnStateStore

_LOGGER = logging.getLogger(__name__)
_EVENT_PHASES: dict[str, Literal["before_run", "after_run"]] = {
    "UserPromptSubmit": "before_run",
    "BeforeRun": "before_run",
    "Stop": "after_run",
    "AfterRun": "after_run",
}


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

    # 会话标识：session_id 是首批宿主字段，conversation_id 是通用合同字段。
    session_id: str | None = Field(default=None, min_length=1)
    conversation_id: str | None = Field(default=None, min_length=1)

    # 轮次标识：Codex、Claude Code 和通用合同各自使用以下一个字段。
    turn_id: str | None = Field(default=None, min_length=1)
    prompt_id: str | None = Field(default=None, min_length=1)
    run_id: str | None = Field(default=None, min_length=1)

    # 内容字段：同时接受首批宿主字段和通用合同字段。
    prompt: str | None = None
    user_input: str | None = None
    last_assistant_message: str | None = None
    final_output: str | None = None

    # 某些宿主在 Stop 再入时提供该字段；主动记忆不据此改变业务行为。
    stop_hook_active: bool = False

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
    """在通用顶层轮次事件和 Memory Hook Bridge 之间接线。"""

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
        await self._retry_one_pending(event)
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

        staged = self._state.stage_capture(
            event.conversation_id,
            event.turn_id,
            final_output=event.final_output,
            observed_at=datetime.now(UTC),
            profile_id=self._settings.profile_id,
        )
        result = await self._deliver_staged(staged)
        warning_code = self._finish_delivery(staged, result)

        log_event(
            _LOGGER,
            logging.INFO,
            "agent_hook.capture.completed",
            attempts=result.attempts,
            created_count=len(result.created_memory_ids),
            pending_count=len(result.pending_review_ids),
            replayed=result.replayed,
            run_reference=run_reference,
            status=result.status,
            failure_code=result.failure_code,
            warning_code=warning_code,
        )
        if warning_code is not None:
            return AgentHookOutcome(warning_code=f"capture_{warning_code}")
        return AgentHookOutcome()

    async def _retry_one_pending(self, event: AgentTurnEvent) -> None:
        """后续 Stop 有界重投一个旧 payload，任何失败都不阻断当前轮次。"""

        pending = self._state.pending_captures(
            exclude=(event.conversation_id, event.turn_id),
            limit=1,
        )
        if not pending:
            return
        state = pending[0]
        run_reference = stable_reference(f"{state.session_id}\x1f{state.turn_id}")
        try:
            result = await self._deliver_staged(state)
            warning_code = self._finish_delivery(state, result)
            log_event(
                _LOGGER,
                logging.INFO,
                "agent_hook.pending_retry.completed",
                attempts=result.attempts,
                run_reference=run_reference,
                status=result.status,
                warning_code=warning_code,
            )
        except Exception as exc:
            log_event(
                _LOGGER,
                logging.ERROR,
                "agent_hook.pending_retry.failed",
                error_type=type(exc).__name__,
                run_reference=run_reference,
            )

    async def _deliver_staged(self, state: TurnState) -> AfterRunResult:
        if state.final_output is None or state.capture_observed_at is None:
            raise TurnStateError("capture_payload_is_not_staged")
        return await self._bridge.after_run_success(
            HookContext(
                conversation_id=state.session_id,
                turn_id=state.turn_id,
                profile_id=state.profile_id,
            ),
            user_input=state.prompt,
            final_output=state.final_output,
            observed_at=state.capture_observed_at,
        )

    def _finish_delivery(
        self,
        state: TurnState,
        result: AfterRunResult,
    ) -> str | None:
        """只有权威终态删除本地 payload，其余状态等待后续 Stop。"""

        if result.status == "completed":
            self._state.delete(state.session_id, state.turn_id)
            return None
        if result.status == "failed":
            self._state.delete(state.session_id, state.turn_id)
            return result.failure_code or "permanent_failure"
        if result.status == "reprocess_required":
            return result.failure_code or "reprocess_required"
        return result.warning_code or "delivery_unavailable"

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
    value = _optional_one_value(values, conflict_code=conflict_code)
    if value is None or not value.strip():
        raise AgentHookInputError(missing_code)
    return value


def _optional_one_value(
    values: tuple[str | None, ...],
    *,
    conflict_code: str,
) -> str | None:
    present = {value for value in values if value is not None}
    if len(present) > 1:
        raise AgentHookInputError(conflict_code)
    return next(iter(present), None)
