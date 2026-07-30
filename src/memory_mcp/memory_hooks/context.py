"""Stable top-level Agent run identity passed to both hooks."""

from pydantic import BaseModel, ConfigDict, Field


class HookContext(BaseModel):
    """Context for exactly one top-level user task, not an internal model step."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    scenario: str = Field(default="general-work", min_length=1)
    subject: str | None = Field(default=None, min_length=1)
    task_intent: str | None = Field(default=None, min_length=1)

    @property
    def run_key(self) -> tuple[str, str, str]:
        return (self.scenario, self.conversation_id, self.turn_id)
