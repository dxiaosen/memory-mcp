"""Memory Core 测试策略和固定输入。"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar

from agent_lab.memory import (
    AssertionKind,
    CreateMemoryCommand,
    LifecycleStatus,
)


@dataclass(frozen=True, slots=True)
class TestScenarioPolicy:
    """只用于验证 Core 扩展边界的中性场景。"""

    __test__: ClassVar[bool] = False

    scenario_id: str = "project-work"
    memory_types: frozenset[str] = frozenset(
        {"preference", "ongoing_item", "stable_context"}
    )
    business_progress_values: frozenset[str] = frozenset({"open", "done"})
    allowed_relations: frozenset[str] = frozenset()
    capture_guidance: str = "Capture durable project-work context."
    relation_rules: dict[str, str] = field(default_factory=dict)
    recall_priorities: dict[str, int] = field(default_factory=dict)


def project_preference_command(
    *,
    lifecycle_status: LifecycleStatus = LifecycleStatus.ACTIVE,
    business_progress: str | None = None,
) -> CreateMemoryCommand:
    return CreateMemoryCommand(
        scenario="project-work",
        subject="weekly-report",
        memory_type="preference",
        content="项目周报默认使用表格",
        assertion_kind=AssertionKind.USER_VIEW,
        lifecycle_status=lifecycle_status,
        conversation_id="session-1",
        source_turn_id="session-1-turn-1",
        source_expression="以后项目周报默认用表格",
        save_rationale="明确且持续有效的用户偏好",
        observed_at=datetime(2026, 7, 29, 10, tzinfo=UTC),
        business_progress=business_progress,
    )
