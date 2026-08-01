"""Memory Core test doubles and fixed inputs."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar

from memory_mcp.core import (
    AssertionKind,
    CandidateDurability,
    CandidateProposal,
    CreateMemoryCommand,
    ExpressionBasis,
    ExtractionRequest,
    LifecycleStatus,
    MemoryMetadataPolicy,
)


@dataclass(frozen=True, slots=True)
class TestMemoryProfile:
    """只用于验证 Core 扩展边界的中性记忆配置。"""

    __test__: ClassVar[bool] = False

    profile_id: str = "project-work"
    memory_types: frozenset[str] = frozenset(
        {"preference", "ongoing_item", "stable_context"}
    )
    business_progress_values: frozenset[str] = frozenset({"open", "done"})
    allowed_relations: frozenset[str] = frozenset()
    capture_guidance: str = "Capture durable project-work context."
    profile_version: str = "project-work-v1"
    relation_rules: dict[str, str] = field(default_factory=dict)
    recall_priorities: dict[str, int] = field(default_factory=dict)
    metadata_policies: dict[str, MemoryMetadataPolicy] = field(
        default_factory=lambda: {
            memory_type: MemoryMetadataPolicy()
            for memory_type in ("preference", "ongoing_item", "stable_context")
        }
    )


@dataclass(frozen=True, slots=True)
class AlternateMemoryProfile:
    """用于证明 Core 不写死 project-work 类型的第二测试配置。"""

    profile_id: str = "personal-notes"
    memory_types: frozenset[str] = frozenset({"note", "commitment"})
    business_progress_values: frozenset[str] = frozenset()
    allowed_relations: frozenset[str] = frozenset()
    capture_guidance: str = "Capture durable personal notes."
    profile_version: str = "personal-notes-v1"
    relation_rules: dict[str, str] = field(default_factory=dict)
    recall_priorities: dict[str, int] = field(default_factory=dict)
    metadata_policies: dict[str, MemoryMetadataPolicy] = field(
        default_factory=lambda: {
            memory_type: MemoryMetadataPolicy()
            for memory_type in ("note", "commitment")
        }
    )


class FakeCandidateExtractor:
    """记录请求并返回预设候选的离线结构化抽取器。"""

    model_id = "fake-structured-model"
    prompt_version = "capture-prompt-v1"
    schema_version = "candidate-v1"

    def __init__(
        self,
        proposals: tuple[CandidateProposal, ...] = (),
        *,
        failures_before_success: int = 0,
    ) -> None:
        self.proposals = proposals
        self.failures_before_success = failures_before_success
        self.requests: list[ExtractionRequest] = []

    def extract(
        self,
        request: ExtractionRequest,
    ) -> tuple[CandidateProposal, ...]:
        self.requests.append(request)
        if len(self.requests) <= self.failures_before_success:
            raise RuntimeError("temporary model interruption")
        return self.proposals


def candidate_proposal(
    source_expression: str,
    *,
    subject: str = "weekly-report",
    memory_type: str = "preference",
    content: str = "项目周报默认使用表格",
    assertion_kind: AssertionKind = AssertionKind.USER_VIEW,
    confidence: float = 0.95,
    durability: CandidateDurability = CandidateDurability.DURABLE,
    expression_basis: ExpressionBasis = ExpressionBasis.EXPLICIT,
    business_progress: str | None = None,
    original_time_expression: str | None = None,
    normalized_time: datetime | None = None,
    save_rationale: str = "测试候选具有跨会话价值",
    proposed_owner_id: str | None = None,
    proposed_conversation_id: str | None = None,
    proposed_source_turn_id: str | None = None,
    proposed_observed_at: datetime | None = None,
) -> CandidateProposal:
    return CandidateProposal(
        subject=subject,
        memory_type=memory_type,
        content=content,
        assertion_kind=assertion_kind,
        source_expression=source_expression,
        save_rationale=save_rationale,
        confidence=confidence,
        durability=durability,
        expression_basis=expression_basis,
        business_progress=business_progress,
        original_time_expression=original_time_expression,
        normalized_time=normalized_time,
        proposed_owner_id=proposed_owner_id,
        proposed_conversation_id=proposed_conversation_id,
        proposed_source_turn_id=proposed_source_turn_id,
        proposed_observed_at=proposed_observed_at,
    )


def project_preference_command(
    *,
    lifecycle_status: LifecycleStatus = LifecycleStatus.ACTIVE,
    business_progress: str | None = None,
) -> CreateMemoryCommand:
    return CreateMemoryCommand(
        profile_id="project-work",
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
