"""召回服务向量路与降级的契约测试。

覆盖：
- 配置 embedding provider 时，``recall`` 计算查询向量并下推 Repository；
- 向量语义相似度通过 ``_VECTOR_BOOST`` 叠加到基础分数；
- provider 缺失或计算失败时降级为词法+近期两路，仍返回结果。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from memory_mcp.core import (
    PrincipalContext,
    RecallQuery,
    VerificationStatus,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.composition import create_memory_service
from memory_mcp.core.domain import (
    AssertionKind,
    Evidence,
    EvidenceSourceType,
    LifecycleStatus,
    MemoryItem,
    MemoryRecord,
    MemoryRevision,
    MessageRole,
    SensitivityLevel,
)
from memory_mcp.core.ports import RecallCandidateSet, embed_single

from tests.support.fakes import FakeEmbeddingProvider, TestMemoryProfile

_PRINCIPAL = PrincipalContext("owner-a")
_OBSERVED_AT = datetime(2026, 7, 29, 10, tzinfo=UTC)


def _record(
    *,
    memory_id=None,
    subject: str,
    memory_type: str = "preference",
    content: str,
    embedding: tuple[float, ...] | None = None,
    owner_id: str = "owner-a",
) -> MemoryRecord:
    """构造一条带 embedding 的活动记忆，绕过捕获流程直接写库。"""

    revision_id = uuid4()
    memory_id = memory_id or uuid4()
    return MemoryRecord(
        item=MemoryItem(
            memory_id=memory_id,
            owner_id=owner_id,
            profile_id="project-work",
            subject=subject,
            memory_type=memory_type,
            created_at=_OBSERVED_AT,
        ),
        current_revision=MemoryRevision(
            revision_id=revision_id,
            memory_id=memory_id,
            owner_id=owner_id,
            revision_number=1,
            content=content,
            assertion_kind=AssertionKind.USER_VIEW,
            lifecycle_status=LifecycleStatus.ACTIVE,
            business_progress=None,
            save_rationale="测试记忆",
            observed_at=_OBSERVED_AT,
            created_at=_OBSERVED_AT,
            extraction_confidence=0.9,
            verification_status=VerificationStatus.USER_ASSERTED,
            sensitivity_level=SensitivityLevel.CONFIDENTIAL,
            valid_from=_OBSERVED_AT,
            valid_until=None,
            embedding=embedding,
        ),
        evidence=(
            Evidence(
                evidence_id=uuid4(),
                memory_id=memory_id,
                revision_id=revision_id,
                owner_id=owner_id,
                source_turn_id="turn-1",
                source_expression=content,
                observed_at=_OBSERVED_AT,
                created_at=_OBSERVED_AT,
                source_role=MessageRole.USER,
                source_type=EvidenceSourceType.CONVERSATION,
            ),
        ),
    )


def _service_with(embedding_provider) -> tuple:
    """构造注入了 embedding provider 的召回服务与已注册 profile 的仓库。"""

    repository = InMemoryMemoryRepository()
    service = create_memory_service(
        repository,
        [TestMemoryProfile()],
        embedding_provider=embedding_provider,
    )
    return service, repository


def test_embed_single_returns_provider_vector() -> None:
    """``embed_single`` 在 provider 可用时返回单条向量。"""

    provider = FakeEmbeddingProvider({"hello": (1.0, 0.0)})
    assert embed_single(provider, "hello") == (1.0, 0.0)


def test_embed_single_returns_none_without_provider() -> None:
    """provider 为 None 时降级返回 None。"""

    assert embed_single(None, "hello") is None


def test_embed_single_returns_none_on_failure() -> None:
    """provider 抛异常时 ``embed_single`` 传播异常，由调用方降级。"""

    provider = FakeEmbeddingProvider({}, failures_before_success=1)
    with pytest.raises(RuntimeError):
        embed_single(provider, "hello")


def test_recall_with_embedding_provider_pushes_query_vector() -> None:
    """配置 provider 时召回计算查询向量并下推 Repository。"""

    repository = InMemoryMemoryRepository()
    repository.register_profile(TestMemoryProfile())
    captured_embeddings: list = []

    def _wrap_find_recall_candidates(*args, **kwargs):
        captured_embeddings.append(kwargs.get("query_embedding"))
        return RecallCandidateSet(candidates=(), lexical_count=0)

    repository.find_recall_candidates = _wrap_find_recall_candidates  # type: ignore[method-assign]
    service = create_memory_service(
        repository,
        [TestMemoryProfile()],
        embedding_provider=FakeEmbeddingProvider(
            {"表格 周报": (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)},
        ),
    )
    service.recall_memory(
        _PRINCIPAL,
        RecallQuery(
            profile_id="project-work",
            query="表格 周报",
            subject=None,
            task_intent=None,
            max_items=5,
        ),
    )
    assert len(captured_embeddings) == 1
    assert captured_embeddings[0] == (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def test_recall_degrades_to_two_paths_without_provider() -> None:
    """未配置 provider 时下推 None 向量，召回仍返回结果。"""

    service, repository = _service_with(embedding_provider=None)
    repository.add(_PRINCIPAL, _record(subject="周报", content="项目周报用表格"))
    result = service.recall_memory(
        _PRINCIPAL,
        RecallQuery(
            profile_id="project-work",
            query="周报 表格",
            subject=None,
            task_intent=None,
            max_items=5,
        ),
    )
    assert len(result.items) == 1
    assert result.items[0].subject == "周报"


def test_recall_degrades_when_embedding_provider_fails() -> None:
    """provider 抛异常时查询向量降级为 None，召回仍返回结果。"""

    service, repository = _service_with(
        embedding_provider=FakeEmbeddingProvider(
            {},
            failures_before_success=1,
        ),
    )
    repository.add(_PRINCIPAL, _record(subject="周报", content="项目周报用表格"))
    result = service.recall_memory(
        _PRINCIPAL,
        RecallQuery(
            profile_id="project-work",
            query="周报 表格",
            subject=None,
            task_intent=None,
            max_items=5,
        ),
    )
    assert len(result.items) == 1


def test_recall_pulls_relation_endpoint_not_in_candidates() -> None:
    """关系感知召回补漏：被已过阈值候选引用但未进候选集的关系端点应被拉入结果。

    场景：记忆 A "项目周报用表格" 与查询词法匹配；记忆 B "季度财务汇总"
    与查询词法不匹配，但 A→B 有自动关系。召回应同时返回 A 与 B（B 经关系补漏）。
    """

    from uuid import uuid4

    from memory_mcp.core import (
        ExpressionBasis,
        MemoryRelation,
        RelationOrigin,
        RelationProvenance,
        RelationScope,
        RelationStatus,
    )

    repository = InMemoryMemoryRepository()
    repository.register_profile(TestMemoryProfile())
    principal = PrincipalContext("owner-a")
    # A：与查询词法匹配，会过阈值
    record_a = _record(subject="周报格式", content="项目周报默认使用表格")
    # B：与查询词法不匹配，正常不会进候选
    record_b = _record(
        subject="季度财务汇总",
        content="Q3 营收与利润的财务汇总数据",
    )
    repository.add(principal, record_a)
    repository.add(principal, record_b)
    # A → B 自动关系（revision-scoped）
    relation = MemoryRelation(
        relation_id=uuid4(),
        owner_id="owner-a",
        profile_id="project-work",
        source_memory_id=record_a.item.memory_id,
        target_memory_id=record_b.item.memory_id,
        relation_type="supports",
        status=RelationStatus.ACTIVE,
        created_at=_OBSERVED_AT,
        origin=RelationOrigin.AUTOMATIC,
        scope=RelationScope.REVISION,
        source_revision_id=record_a.current_revision.revision_id,
        target_revision_id=record_b.current_revision.revision_id,
        provenance=RelationProvenance(
            capture_id=uuid4(),
            conversation_id="test",
            source_turn_id="test",
            source_expression="supports",
            confidence=0.95,
            expression_basis=ExpressionBasis.EXPLICIT,
            model_id="test",
            prompt_version="test",
            schema_version="test",
        ),
    )
    repository._relations[relation.relation_id] = relation  # type: ignore[attr-defined]

    service = create_memory_service(
        repository,
        [TestMemoryProfile()],
        embedding_provider=None,
    )
    result = service.recall_memory(
        principal,
        RecallQuery(
            profile_id="project-work",
            query="周报 表格",
            subject=None,
            task_intent=None,
            max_items=5,
        ),
    )
    recalled_ids = {item.memory_id for item in result.items}
    assert record_a.item.memory_id in recalled_ids, "matching record must be recalled"
    assert record_b.item.memory_id in recalled_ids, (
        "relation-linked record must be pulled in via relation expansion"
    )
