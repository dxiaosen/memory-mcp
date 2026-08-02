"""两个 Hook 共享的稳定顶层 Agent 任务标识。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class HookContext(BaseModel):
    """恰好一个顶层用户任务的上下文，不代表内部模型步骤。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    profile_id: str | None = Field(default=None, min_length=1)
    subject: str | None = Field(default=None, min_length=1)
    task_intent: str | None = Field(default=None, min_length=1)

    @property
    def run_key(self) -> tuple[str, str, str]:
        return (
            self.profile_id or "<server-default>",
            self.conversation_id,
            self.turn_id,
        )
