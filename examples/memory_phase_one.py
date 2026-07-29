"""阶段一离线演示：通用记忆卡片和用户隔离。"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agent_lab.config import get_logging_settings
from agent_lab.memory import (
    AssertionKind,
    CreateMemoryCommand,
    LifecycleStatus,
    MemoryNotFoundError,
    PrincipalContext,
)
from agent_lab.memory.adapters.sqlite import (
    SQLiteMemoryRepository,
    connection_factory,
)
from agent_lab.memory.adapters.sqlite.runtime import apply_migrations, check_health
from agent_lab.memory.composition import create_memory_service
from agent_lab.observability import configure_logging_from_settings


@dataclass(frozen=True, slots=True)
class DemoScenarioPolicy:
    """仅用于中性演示，不代表正式业务场景。"""

    scenario_id: str = "project-work"
    memory_types: frozenset[str] = frozenset(
        {"preference", "ongoing_item", "stable_context"}
    )
    business_progress_values: frozenset[str] = frozenset()
    allowed_relations: frozenset[str] = frozenset()
    capture_guidance: str = "Remember durable project-work context."
    relation_rules: dict[str, str] = field(default_factory=dict)
    recall_priorities: dict[str, int] = field(default_factory=dict)


def main() -> None:
    """使用 SQLite 创建记忆，并证明另一用户不能越权读取。"""

    configure_logging_from_settings(get_logging_settings())
    demo_directory = Path(".agent-lab/demo-memory")
    demo_directory.mkdir(parents=True, exist_ok=True)
    database_path = demo_directory / f"{uuid4().hex}.db"
    try:
        apply_migrations(database_path)
        check_health(database_path)
        service = create_memory_service(
            SQLiteMemoryRepository(connection_factory(database_path)),
            [DemoScenarioPolicy()],
        )
        analyst_a = PrincipalContext("analyst-a")
        analyst_b = PrincipalContext("analyst-b")
        memory = service.create_memory(
            analyst_a,
            CreateMemoryCommand(
                scenario="project-work",
                subject="weekly-report",
                memory_type="preference",
                content="项目周报默认使用表格",
                assertion_kind=AssertionKind.USER_VIEW,
                lifecycle_status=LifecycleStatus.ACTIVE,
                conversation_id="demo-session-1",
                source_turn_id="demo-session-1-turn-1",
                source_expression="以后项目周报默认用表格",
                save_rationale="明确且持续有效的用户偏好",
                observed_at=datetime.now(UTC),
            ),
        )
        loaded = service.get_memory(analyst_a, memory.item.memory_id)
        print(
            "Created memory:",
            f"owner={loaded.item.owner_id}",
            f"scenario={loaded.item.scenario}",
            f"type={loaded.item.memory_type}",
            f"status={loaded.current_revision.lifecycle_status.value}",
            f"evidence={len(loaded.evidence)}",
        )

        try:
            service.get_memory(analyst_b, memory.item.memory_id)
        except MemoryNotFoundError:
            print("Cross-user identifier lookup is safely unavailable.")
    finally:
        database_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
