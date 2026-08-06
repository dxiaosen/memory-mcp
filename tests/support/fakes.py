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
    MemoryExpiryDerivation,
    MemoryMetadataPolicy,
    MemoryRelationPolicy,
    RelationExtractionRequest,
    RelationProposal,
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
    capture_guidance: str = "Capture durable project-work context."
    profile_version: str = "project-work-v1"
    relation_policies: dict[str, MemoryRelationPolicy] = field(default_factory=dict)
    recall_priorities: dict[str, int] = field(
        default_factory=lambda: {
            "preference": 30,
            "ongoing_item": 20,
            "stable_context": 10,
        }
    )
    recall_hints: dict[str, frozenset[str]] = field(
        default_factory=lambda: {
            "preference": frozenset({"偏好", "默认"}),
            "ongoing_item": frozenset({"下一步", "继续"}),
            "stable_context": frozenset({"背景", "现状"}),
        }
    )
    metadata_policies: dict[str, MemoryMetadataPolicy] = field(
        default_factory=lambda: {
            memory_type: MemoryMetadataPolicy()
            for memory_type in ("preference", "ongoing_item", "stable_context")
        }
    )
    timeline_relation_types: frozenset[str] = field(default_factory=frozenset)
    expiry_derivations: dict[str, MemoryExpiryDerivation] = field(
        default_factory=dict
    )


@dataclass(frozen=True, slots=True)
class AlternateMemoryProfile:
    """用于证明 Core 不写死 project-work 类型的第二测试配置。"""

    profile_id: str = "personal-notes"
    memory_types: frozenset[str] = frozenset({"note", "commitment"})
    business_progress_values: frozenset[str] = frozenset()
    capture_guidance: str = "Capture durable personal notes."
    profile_version: str = "personal-notes-v1"
    relation_policies: dict[str, MemoryRelationPolicy] = field(default_factory=dict)
    recall_priorities: dict[str, int] = field(
        default_factory=lambda: {"note": 20, "commitment": 30}
    )
    recall_hints: dict[str, frozenset[str]] = field(
        default_factory=lambda: {
            "note": frozenset({"笔记"}),
            "commitment": frozenset({"承诺", "约定"}),
        }
    )
    metadata_policies: dict[str, MemoryMetadataPolicy] = field(
        default_factory=lambda: {
            memory_type: MemoryMetadataPolicy()
            for memory_type in ("note", "commitment")
        }
    )
    timeline_relation_types: frozenset[str] = field(default_factory=frozenset)
    expiry_derivations: dict[str, MemoryExpiryDerivation] = field(
        default_factory=dict
    )


class FakeCandidateExtractor:
    """记录请求并返回预设候选的离线结构化抽取器。

    每次 ``extract`` 返回构造时传入的全部 proposals（适合单轮捕获）。
    多轮捕获逐条产出用 :class:`SequentialCandidateExtractor`。
    """

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


class SequentialCandidateExtractor:
    """每次 extract 返回预设队列的下一条候选，模拟多轮捕获逐条产出。

    与 :class:`FakeCandidateExtractor` 互补：后者每次返回全部 proposals，
    本类按调用顺序逐条弹出，适合需要多轮不同候选的端到端场景。
    """

    model_id = "fake-structured-model"
    prompt_version = "capture-prompt-v1"
    schema_version = "candidate-v1"

    def __init__(self, proposals: tuple[CandidateProposal, ...]) -> None:
        self._queue = list(proposals)
        self.requests: list[ExtractionRequest] = []

    def extract(
        self,
        request: ExtractionRequest,
    ) -> tuple[CandidateProposal, ...]:
        self.requests.append(request)
        if not self._queue:
            return ()
        return (self._queue.pop(0),)


class FakeRelationExtractor:
    """记录关系请求并允许按可信端点动态生成建议。"""

    model_id = "fake-relation-model"
    prompt_version = "relation-prompt-v1"
    schema_version = "relation-v1"

    def __init__(
        self,
        proposal_factory=None,
        *,
        failures_before_success: int = 0,
    ) -> None:
        self.proposal_factory = proposal_factory or (lambda request: ())
        self.failures_before_success = failures_before_success
        self.requests: list[RelationExtractionRequest] = []

    def extract(
        self,
        request: RelationExtractionRequest,
    ) -> tuple[RelationProposal, ...]:
        self.requests.append(request)
        if len(self.requests) <= self.failures_before_success:
            raise RuntimeError("temporary relation model interruption")
        return tuple(self.proposal_factory(request))


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


class FakeEmbeddingProvider:
    """返回固定向量的确定性 embedding 提供者，供召回向量路测试使用。

    按文本到向量的映射返回；未映射的文本返回零向量，使相似度测试稳定可控。
    实现 ``EmbeddingProvider`` 端口契约（model_id/dimensions/embed）。
    """

    model_id = "fake-embedding-model"
    dimensions = 8

    def __init__(
        self,
        vectors: dict[str, tuple[float, ...]],
        *,
        failures_before_success: int = 0,
    ) -> None:
        self._vectors = dict(vectors)
        self.failures_before_success = failures_before_success
        self.calls = 0

    def embed(
        self,
        texts: tuple[str, ...],
    ) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        if self.calls <= self.failures_before_success:
            raise RuntimeError("temporary embedding interruption")
        zero = tuple(0.0 for _ in range(self.dimensions))
        return tuple(self._vectors.get(text, zero) for text in texts)
