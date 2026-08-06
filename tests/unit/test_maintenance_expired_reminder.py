"""A2 过期证据依赖链提醒（expiry_derivations）的契约测试。

覆盖：
- supports 关系的 evidence_claim 端点过期后，维护循环派生一条 ongoing_research
  提醒记忆，subject = thesis subject，内容含 {endpoint_subject}/{thesis_subject}；
- 同 focus thesis 已有活动 ongoing_research 提醒时跳过（去重）；
- threatens 关系端点过期触发对应模板；
- Profile 未声明 expiry_derivations 时不派生提醒。
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
    MemoryRelation,
    MemoryRevision,
    MemoryService,
    MessageRole,
    PrincipalContext,
    ProfileRegistry,
    RelationOrigin,
    RelationScope,
    RelationStatus,
    SensitivityLevel,
    VerificationStatus,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.adapters.sensitive import RegexSensitiveContentGuard
from memory_mcp.core.domain import Evidence
from memory_mcp.profiles import GeneralWorkProfile, InvestmentResearchProfile

_PRINCIPAL = PrincipalContext("owner-a")
_NOW = datetime(2026, 8, 6, 10, tzinfo=UTC)


def _record(
    *,
    subject: str,
    content: str,
    memory_type: str,
    observed_at: datetime,
    valid_until: datetime,
    profile_id: str = "investment-research",
) -> MemoryRecord:
    """构造一条记忆，valid_until 可调以触发过期。"""

    memory_id = uuid4()
    revision_id = uuid4()
    return MemoryRecord(
        item=MemoryItem(
            memory_id=memory_id,
            owner_id="owner-a",
            profile_id=profile_id,
            subject=subject,
            memory_type=memory_type,
            created_at=observed_at,
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
            save_rationale="测试",
            observed_at=observed_at,
            created_at=observed_at,
            extraction_confidence=0.9,
            verification_status=VerificationStatus.USER_ASSERTED,
            sensitivity_level=SensitivityLevel.CONFIDENTIAL,
            valid_from=observed_at,
            valid_until=valid_until,
        ),
        evidence=(
            Evidence(
                evidence_id=uuid4(),
                memory_id=memory_id,
                revision_id=revision_id,
                owner_id="owner-a",
                source_turn_id="turn-1",
                source_expression=content,
                observed_at=observed_at,
                created_at=observed_at,
                source_role=MessageRole.USER,
                source_type=EvidenceSourceType.CONVERSATION,
            ),
        ),
    )


def _manual_relation(
    *,
    source: MemoryRecord,
    target: MemoryRecord,
    relation_type: str,
) -> MemoryRelation:
    return MemoryRelation(
        relation_id=uuid4(),
        owner_id="owner-a",
        profile_id="investment-research",
        source_memory_id=source.item.memory_id,
        target_memory_id=target.item.memory_id,
        relation_type=relation_type,
        status=RelationStatus.ACTIVE,
        created_at=_NOW - timedelta(days=100),
        origin=RelationOrigin.MANUAL,
        scope=RelationScope.ITEM,
        source_revision_id=source.current_revision.revision_id,
        target_revision_id=target.current_revision.revision_id,
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


def test_expired_evidence_supports_derives_ongoing_research_reminder() -> None:
    """evidence_claim supports thesis，证据过期后派生 ongoing_research 提醒。"""

    service, repository = _service()
    base = _NOW - timedelta(days=120)
    thesis = _record(
        subject="example-co-demand",
        content="示例公司企业需求持续增长",
        memory_type="thesis",
        observed_at=base,
        valid_until=base + timedelta(days=365),
    )
    evidence = _record(
        subject="example-co-q2-beat",
        content="Q2 营收超预期验证需求",
        memory_type="evidence_claim",
        observed_at=base,
        valid_until=_NOW - timedelta(days=1),  # 维护时已过期
    )
    repository.add(_PRINCIPAL, thesis)
    repository.add(_PRINCIPAL, evidence)
    # 在证据仍有效时建关系（valid_until 之前），维护循环再让端点过期。
    repository.link_relation(
        _PRINCIPAL,
        _manual_relation(source=evidence, target=thesis, relation_type="supports"),
        effective_at=base + timedelta(days=1),
    )

    result = service.run_maintenance()

    assert result.stale_relation_count == 1
    reminders = service.list_memories(_PRINCIPAL)
    ongoing = [
        record
        for record in reminders
        if record.item.memory_type == "ongoing_research"
    ]
    assert len(ongoing) == 1, "应派生一条 ongoing_research 提醒"
    reminder = ongoing[0]
    assert reminder.item.subject == "example-co-demand"
    assert "example-co-q2-beat" in reminder.current_revision.content
    assert "example-co-demand" in reminder.current_revision.content
    assert "过期" in reminder.current_revision.content
    # 系统提醒来源标记
    assert reminder.evidence[0].conversation_id == "system:maintenance"
    assert reminder.evidence[0].source_type.value == "system"


def test_reminder_skipped_when_active_ongoing_research_already_exists() -> None:
    """同 focus thesis 已有活动 ongoing_research 时跳过，不重复派生。"""

    service, repository = _service()
    base = _NOW - timedelta(days=120)
    thesis = _record(
        subject="dupe-thesis",
        content="去重测试论点",
        memory_type="thesis",
        observed_at=base,
        valid_until=base + timedelta(days=365),
    )
    existing_reminder = _record(
        subject="dupe-thesis",
        content="已有提醒：需复核该论点",
        memory_type="ongoing_research",
        observed_at=base,
        valid_until=base + timedelta(days=365),
    )
    evidence = _record(
        subject="dupe-evidence",
        content="去重测试证据",
        memory_type="evidence_claim",
        observed_at=base,
        valid_until=_NOW - timedelta(days=1),
    )
    for record in (thesis, existing_reminder, evidence):
        repository.add(_PRINCIPAL, record)
    repository.link_relation(
        _PRINCIPAL,
        _manual_relation(source=evidence, target=thesis, relation_type="supports"),
        effective_at=base + timedelta(days=1),
    )

    service.run_maintenance()

    ongoing = [
        record
        for record in service.list_memories(_PRINCIPAL)
        if record.item.memory_type == "ongoing_research"
    ]
    assert len(ongoing) == 1, "已有 ongoing_research 时不应再派生"


def test_threatens_expired_evidence_uses_threatens_template() -> None:
    """risk threatens thesis，风险过期后用 threatens 模板派生提醒。"""

    service, repository = _service()
    base = _NOW - timedelta(days=120)
    thesis = _record(
        subject="risk-thesis",
        content="风险相关测试论点",
        memory_type="thesis",
        observed_at=base,
        valid_until=base + timedelta(days=365),
    )
    risk = _record(
        subject="competition-risk",
        content="竞品进入企业市场",
        memory_type="risk",
        observed_at=base,
        valid_until=_NOW - timedelta(days=1),
    )
    repository.add(_PRINCIPAL, thesis)
    repository.add(_PRINCIPAL, risk)
    repository.link_relation(
        _PRINCIPAL,
        _manual_relation(source=risk, target=thesis, relation_type="threatens"),
        effective_at=base + timedelta(days=1),
    )

    service.run_maintenance()

    ongoing = [
        record
        for record in service.list_memories(_PRINCIPAL)
        if record.item.memory_type == "ongoing_research"
    ]
    assert len(ongoing) == 1
    assert "风险" in ongoing[0].current_revision.content
    assert "消除" in ongoing[0].current_revision.content


def test_general_work_profile_does_not_derive_reminders() -> None:
    """general-work 未声明 expiry_derivations，过期不派生提醒。"""

    repository = InMemoryMemoryRepository()
    service = MemoryService(
        repository,
        ProfileRegistry(),
        sensitive_guard=RegexSensitiveContentGuard(),
        clock=lambda: _NOW,
    )
    service.register_profile(GeneralWorkProfile())
    base = _NOW - timedelta(days=120)
    # general-work 无关系策略，直接验证不派生：过期记忆本身不产生提醒。
    expired = _record(
        subject="generic-preference",
        content="通用偏好",
        memory_type="preference",
        observed_at=base,
        valid_until=_NOW - timedelta(days=1),
        profile_id="general-work",
    )
    repository.add(_PRINCIPAL, expired)
    result = service.run_maintenance()
    assert result.expired_memory_count == 1
    ongoing = [
        record
        for record in service.list_memories(_PRINCIPAL, include_inactive=True)
        if record.item.memory_type == "ongoing_research"
    ]
    assert ongoing == []
