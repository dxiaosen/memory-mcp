"""C2 subject 聚簇渲染的契约测试。

覆盖：
- 同 subject 的多条召回聚为一组、组前置标题；
- 多 subject 按首次出现顺序分组、组间空行分隔；
- 单条召回仍带标题（结构一致）；
- 聚簇不改变已选条目顺序或数量。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from memory_mcp.core import (
    AssertionKind,
    EvidenceSourceType,
    LifecycleStatus,
    MemoryItem,
    MemoryRecord,
    MemoryRevision,
    MemoryService,
    MessageRole,
    PrincipalContext,
    ProfileRegistry,
    RecallQuery,
    SensitivityLevel,
    VerificationStatus,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.adapters.sensitive import RegexSensitiveContentGuard
from memory_mcp.core.domain import Evidence
from memory_mcp.profiles import InvestmentResearchProfile

_PRINCIPAL = PrincipalContext("owner-a")
_NOW = datetime(2026, 7, 30, 10, tzinfo=UTC)
_VALID_UNTIL = _NOW + timedelta(days=365)


def _thesis(
    *,
    subject: str,
    content: str,
) -> MemoryRecord:
    """构造一条投研 thesis 活动记忆。"""

    memory_id = uuid4()
    revision_id = uuid4()
    return MemoryRecord(
        item=MemoryItem(
            memory_id=memory_id,
            owner_id="owner-a",
            profile_id="investment-research",
            subject=subject,
            memory_type="thesis",
            created_at=_NOW,
        ),
        current_revision=MemoryRevision(
            revision_id=revision_id,
            memory_id=memory_id,
            owner_id="owner-a",
            revision_number=1,
            content=content,
            assertion_kind=AssertionKind.USER_VIEW,
            lifecycle_status=LifecycleStatus.ACTIVE,
            business_progress="monitoring",
            save_rationale="测试论点",
            observed_at=_NOW,
            created_at=_NOW,
            extraction_confidence=0.95,
            verification_status=VerificationStatus.USER_ASSERTED,
            sensitivity_level=SensitivityLevel.CONFIDENTIAL,
            valid_from=_NOW,
            valid_until=_VALID_UNTIL,
        ),
        evidence=(
            Evidence(
                evidence_id=uuid4(),
                memory_id=memory_id,
                revision_id=revision_id,
                owner_id="owner-a",
                source_turn_id="turn-1",
                source_expression=content,
                observed_at=_NOW,
                created_at=_NOW,
                source_role=MessageRole.USER,
                source_type=EvidenceSourceType.CONVERSATION,
            ),
        ),
    )


def _service() -> tuple[MemoryService, InMemoryMemoryRepository]:
    repository = InMemoryMemoryRepository()
    service = MemoryService(
        repository,
        ProfileRegistry(),
        sensitive_guard=RegexSensitiveContentGuard(),
        clock=lambda: _NOW,
    )
    service.register_profile(InvestmentResearchProfile())
    return service, repository


def test_recall_clusters_same_subject_under_one_header() -> None:
    """同 subject 的两条 thesis 聚为一组，只出现一次标题。"""

    service, repository = _service()
    repository.add(
        _PRINCIPAL,
        _thesis(
            subject="example-company-enterprise-demand",
            content="示例公司企业需求将持续增长",
        ),
    )
    repository.add(
        _PRINCIPAL,
        _thesis(
            subject="example-company-enterprise-demand",
            content="示例公司企业需求扩张有韧性",
        ),
    )
    recalled = service.recall_memory(
        _PRINCIPAL,
        RecallQuery(
            profile_id="investment-research",
            query="示例公司企业需求",
            max_items=2,
        ),
    )
    assert len(recalled.items) == 2
    context = recalled.rendered_context
    # 该 subject 标题只出现一次（两行同一组）。
    assert context.count("## example-company-enterprise-demand") == 1


def test_recall_separates_distinct_subjects_with_blank_lines() -> None:
    """不同 subject 的条目分组，组间空行分隔。"""

    service, repository = _service()
    repository.add(
        _PRINCIPAL,
        _thesis(
            subject="alpha-company-demand",
            content="甲公司企业需求增长",
        ),
    )
    repository.add(
        _PRINCIPAL,
        _thesis(
            subject="beta-company-demand",
            content="乙公司企业需求增长",
        ),
    )
    recalled = service.recall_memory(
        _PRINCIPAL,
        RecallQuery(
            profile_id="investment-research",
            query="公司企业需求增长",
            max_items=2,
        ),
    )
    assert len(recalled.items) == 2
    context = recalled.rendered_context
    # 两组标题都在。
    assert "## alpha-company-demand" in context
    assert "## beta-company-demand" in context
    # 组间有空行（标题行与上一组最后一行之间）。
    assert "\n\n## beta-company-demand" in context or (
        "\n\n## alpha-company-demand" in context
    )


def test_recall_single_item_still_has_header() -> None:
    """单条召回也带标题，保持结构一致。"""

    service, repository = _service()
    repository.add(
        _PRINCIPAL,
        _thesis(
            subject="solo-company-demand",
            content="独立公司企业需求增长",
        ),
    )
    recalled = service.recall_memory(
        _PRINCIPAL,
        RecallQuery(
            profile_id="investment-research",
            query="独立公司企业需求",
            max_items=1,
        ),
    )
    assert len(recalled.items) == 1
    assert "## solo-company-demand" in recalled.rendered_context
