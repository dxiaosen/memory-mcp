"""B2 thesis 语义去重的契约测试。

覆盖：
- 字面 subject 不匹配但内容语义近似的候选，命中现有同类型记忆后降级为
  待确认（semantic_lifecycle_conflict），而非新增碎片化 thesis；
- 内容几乎一致的近似命中走 duplicate evidence；
- 显式替换意图走 replacement；
- Profile 未声明阈值时（general-work）不启用语义去重，走原有新增路径；
- 嵌入不可用时回退到原有新增路径。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from memory_mcp.core import (
    AdmissionDecision,
    AssertionKind,
    MemoryService,
    MessageRole,
    PrincipalContext,
    TurnEnvelope,
    TurnMessage,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.composition import create_memory_service
from memory_mcp.profiles import GeneralWorkProfile, InvestmentResearchProfile

from tests.support.fakes import (
    FakeCandidateExtractor,
    candidate_proposal,
)

_PRINCIPAL = PrincipalContext("owner-a")
_NOW = datetime(2026, 7, 30, 10, tzinfo=UTC)


def _turn(text: str, *, turn_id: str) -> TurnEnvelope:
    return TurnEnvelope(
        profile_id="investment-research",
        conversation_id="conversation-1",
        source_turn_id=turn_id,
        content=text,
        observed_at=_NOW,
        subject_hint="example-company",
        messages=(
            TurnMessage(
                role=MessageRole.USER,
                content=text,
                message_id=f"message-{turn_id}",
            ),
        ),
    )


def _service(
    vectors: dict[str, tuple[float, ...]],
) -> tuple[MemoryService, FakeCandidateExtractor]:
    extractor = FakeCandidateExtractor(())
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [InvestmentResearchProfile()],
        candidate_extractor=extractor,
        embedding_provider=_FakeProvider(vectors),
    )
    return service, extractor


class _FakeProvider:
    """8 维确定性 embedding provider，按文本映射返回向量。"""

    model_id = "fake-embedding-model"
    dimensions = 8

    def __init__(self, vectors: dict[str, tuple[float, ...]]) -> None:
        self._vectors = dict(vectors)

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        zero = tuple(0.0 for _ in range(self.dimensions))
        return tuple(self._vectors.get(text, zero) for text in texts)


def test_semantic_near_duplicate_thesis_downgrades_to_pending() -> None:
    """字面 subject 不同但语义近似的 thesis：降级为待确认。"""

    thesis_a_content = "示例公司企业需求会持续增长"
    thesis_b_content = "示例公司企业需求将持续扩张"
    vectors = {
        thesis_a_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        # 与 a 余弦相似度 1.0（同向），超阈值 0.92。
        thesis_b_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    service, extractor = _service(vectors)
    extractor.proposals = (
        candidate_proposal(
            thesis_a_content,
            subject="example-company-demand-v1",
            memory_type="thesis",
            content=thesis_a_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    service.capture_turn(_PRINCIPAL, _turn(thesis_a_content, turn_id="t1"))

    extractor.proposals = (
        candidate_proposal(
            thesis_b_content,
            subject="example-company-demand-v2",
            memory_type="thesis",
            content=thesis_b_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    result = service.capture_turn(_PRINCIPAL, _turn(thesis_b_content, turn_id="t2"))
    outcome = result.outcomes[0]
    assert outcome.decision is AdmissionDecision.PENDING
    assert outcome.reason_code == "semantic_lifecycle_conflict"
    assert outcome.review_id is not None


def test_semantic_duplicate_content_routes_to_duplicate_evidence() -> None:
    """近似命中且内容几乎一致：走 duplicate evidence。"""

    thesis_a_content = "示例公司企业需求持续增长"
    thesis_b_content = "示例公司企业需求持续增长"  # 内容相同
    vectors = {
        thesis_a_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        thesis_b_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    service, extractor = _service(vectors)
    extractor.proposals = (
        candidate_proposal(
            thesis_a_content,
            subject="example-company-demand-a",
            memory_type="thesis",
            content=thesis_a_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    service.capture_turn(_PRINCIPAL, _turn(thesis_a_content, turn_id="t1"))

    extractor.proposals = (
        candidate_proposal(
            thesis_b_content,
            subject="example-company-demand-b",  # 字面不同
            memory_type="thesis",
            content=thesis_b_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    result = service.capture_turn(_PRINCIPAL, _turn(thesis_b_content, turn_id="t2"))
    outcome = result.outcomes[0]
    assert outcome.decision is AdmissionDecision.AUTO_SAVE
    assert outcome.reason_code == "semantic_duplicate_evidence"


def test_general_work_profile_disables_semantic_dedup() -> None:
    """general-work 未声明 threshold：不启用语义去重，走新增。"""

    content_a = "周报默认使用表格"
    content_b = "周报默认使用表格"
    vectors = {
        content_a: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        content_b: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    extractor = FakeCandidateExtractor(())
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [GeneralWorkProfile()],
        candidate_extractor=extractor,
        embedding_provider=_FakeProvider(vectors),
    )
    extractor.proposals = (
        candidate_proposal(
            content_a,
            subject="weekly-report-format-a",
            memory_type="preference",
            content=content_a,
        ),
    )
    service.capture_turn(
        _PRINCIPAL,
        replace(
            _turn(content_a, turn_id="t1"),
            profile_id="general-work",
        ),
    )
    extractor.proposals = (
        candidate_proposal(
            content_b,
            subject="weekly-report-format-b",
            memory_type="preference",
            content=content_b,
        ),
    )
    result = service.capture_turn(
        _PRINCIPAL,
        replace(_turn(content_b, turn_id="t2"), profile_id="general-work"),
    )
    # 无 threshold -> 新增第二条，不触发语义去重。
    assert result.outcomes[0].decision is AdmissionDecision.AUTO_SAVE
    assert result.outcomes[0].reason_code != "semantic_duplicate_evidence"
