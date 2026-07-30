"""阶段二离线演示：四类准入、幂等捕获和 pending 确认。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from memory_mcp.config import get_logging_settings
from memory_mcp.core import (
    AdmissionDecision,
    ExtractionRequest,
    PrincipalContext,
    TurnEnvelope,
)
from memory_mcp.core.adapters import StructuredCandidateExtractor
from memory_mcp.core.adapters.sqlite import (
    SQLiteMemoryRepository,
    connection_factory,
)
from memory_mcp.core.adapters.sqlite.runtime import apply_migrations, check_health
from memory_mcp.core.composition import create_memory_service
from memory_mcp.logging import configure_logging_from_settings


@dataclass(frozen=True, slots=True)
class DemoCapturePolicy:
    """中性演示策略，不代表正式业务场景。"""

    scenario_id: str = "project-work"
    memory_types: frozenset[str] = frozenset(
        {"preference", "ongoing_item", "stable_context"}
    )
    business_progress_values: frozenset[str] = frozenset({"open", "done"})
    allowed_relations: frozenset[str] = frozenset()
    capture_guidance: str = "Capture durable project-work context."
    policy_version: str = "project-work-v1"
    relation_rules: dict[str, str] = field(default_factory=dict)
    recall_priorities: dict[str, int] = field(default_factory=dict)


def offline_structured_backend(
    request: ExtractionRequest,
) -> Sequence[Mapping[str, Any]]:
    """返回固定结构化结果，用于离线演示 Core，不模拟模型智能。"""

    return (
        {
            "subject": "weekly-report",
            "memory_type": "preference",
            "content": "项目周报默认使用表格",
            "assertion_kind": "user_view",
            "source_expression": "以后项目周报默认用表格",
            "save_rationale": "明确且持续有效的用户偏好",
            "confidence": 0.98,
            "durability": "durable",
            "expression_basis": "explicit",
        },
        {
            "subject": "interface-refactor",
            "memory_type": "ongoing_item",
            "content": "接口重构下周继续跟进",
            "assertion_kind": "user_provided_fact",
            "source_expression": "接口重构下周还要继续跟进",
            "save_rationale": "跨会话仍需推进",
            "confidence": 0.95,
            "durability": "durable",
            "expression_basis": "explicit",
            "business_progress": "open",
            "original_time_expression": "下周",
            "normalized_time": "2026-08-03T00:00:00+08:00",
        },
        {
            "subject": "current-answer",
            "memory_type": "preference",
            "content": "当前回答使用简短格式",
            "assertion_kind": "user_view",
            "source_expression": "这次回答短一点",
            "save_rationale": "只约束当前回答",
            "confidence": 0.99,
            "durability": "temporary",
            "expression_basis": "explicit",
        },
        {
            "subject": "color-style",
            "memory_type": "preference",
            "content": "用户可能偏好蓝色",
            "assertion_kind": "system_inference",
            "source_expression": "我可能偏好蓝色",
            "save_rationale": "弱推断需要用户确认",
            "confidence": 0.72,
            "durability": "durable",
            "expression_basis": "inferred",
        },
    )


def main() -> None:
    """运行不调用外部模型的阶段二完整流程。"""

    configure_logging_from_settings(get_logging_settings())
    demo_directory = Path(".memory-mcp/demo-memory")
    demo_directory.mkdir(parents=True, exist_ok=True)
    database_path = demo_directory / f"{uuid4().hex}.db"
    try:
        apply_migrations(database_path)
        check_health(database_path)
        extractor = StructuredCandidateExtractor(
            offline_structured_backend,
            model_id="deterministic-offline-demo",
            prompt_version="capture-prompt-v1",
        )
        service = create_memory_service(
            SQLiteMemoryRepository(connection_factory(database_path)),
            [DemoCapturePolicy()],
            candidate_extractor=extractor,
        )
        principal = PrincipalContext("analyst-a")
        turn = TurnEnvelope(
            scenario="project-work",
            conversation_id="demo-session-2",
            source_turn_id="demo-session-2-turn-1",
            content=(
                "以后项目周报默认用表格。"
                "接口重构下周还要继续跟进。"
                "这次回答短一点。"
                "我可能偏好蓝色。"
                "密码是 fictional-demo-secret。"
            ),
            observed_at=datetime(2026, 7, 29, 10, tzinfo=UTC),
        )

        result = service.capture_turn(principal, turn)
        counts = {
            decision.value: sum(
                outcome.decision is decision for outcome in result.outcomes
            )
            for decision in AdmissionDecision
        }
        print("Capture status:", result.status.value)
        print("Admission counts:", counts)
        print("Active memories:", len(service.list_memories(principal)))
        pending = service.list_pending_reviews(principal)
        print("Pending reviews:", len(pending))

        if pending:
            confirmed = service.confirm_review(principal, pending[0].review_id)
            print(
                "Confirmed memory:",
                confirmed.item.memory_type,
                confirmed.current_revision.content,
            )

        replayed = service.capture_turn(principal, turn)
        print(
            "Idempotent replay:",
            replayed.replayed,
            "active memories:",
            len(service.list_memories(principal)),
        )
        other_user = PrincipalContext("analyst-b")
        print(
            "Other user's pending reviews:",
            len(service.list_pending_reviews(other_user)),
        )
    finally:
        database_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
