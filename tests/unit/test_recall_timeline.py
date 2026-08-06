"""A1 投研时间线召回（recall_timeline）的契约测试。

覆盖：
- thesis + supports/challenges/risk 演进链按 observed_at 升序返回；
- 深度截断（_TIMELINE_MAX_DEPTH=3）只取近邻；
- 环防护（双向关系不重复展开同一端点）；
- focus 不存在或跨 Profile 时返回空结果；
- Profile 未声明 timeline_relation_types 时返回空结果。
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
    TimelineQuery,
    VerificationStatus,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.adapters.sensitive import RegexSensitiveContentGuard
from memory_mcp.core.domain import Evidence
from memory_mcp.profiles import GeneralWorkProfile, InvestmentResearchProfile

_PRINCIPAL = PrincipalContext("owner-a")
_NOW = datetime(2026, 8, 6, 10, tzinfo=UTC)
_VALID_UNTIL = _NOW + timedelta(days=365)


def _record(
    *,
    subject: str,
    content: str,
    memory_type: str = "thesis",
    observed_at: datetime,
) -> MemoryRecord:
    """构造一条投研活动记忆，observed_at 可调以验证时序。"""

    memory_id = uuid4()
    revision_id = uuid4()
    return MemoryRecord(
        item=MemoryItem(
            memory_id=memory_id,
            owner_id="owner-a",
            profile_id="investment-research",
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
            save_rationale="测试论点",
            observed_at=observed_at,
            created_at=observed_at,
            extraction_confidence=0.95,
            verification_status=VerificationStatus.USER_ASSERTED,
            sensitivity_level=SensitivityLevel.CONFIDENTIAL,
            valid_from=observed_at,
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
    created_at: datetime,
) -> MemoryRelation:
    """构造一条 item-scoped 手动关系（revision snapshots 成对提供）。"""

    return MemoryRelation(
        relation_id=uuid4(),
        owner_id="owner-a",
        profile_id="investment-research",
        source_memory_id=source.item.memory_id,
        target_memory_id=target.item.memory_id,
        relation_type=relation_type,
        status=RelationStatus.ACTIVE,
        created_at=created_at,
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


def _link(
    repository: InMemoryMemoryRepository,
    relation: MemoryRelation,
) -> None:
    repository.link_relation(
        _PRINCIPAL,
        relation,
        effective_at=_NOW,
    )


def test_timeline_returns_hops_sorted_by_observed_at_ascending() -> None:
    """thesis 焦点 + 3 条演进端点，按 observed_at 升序返回。

    关系方向遵循 Profile 策略：evidence_claim→thesis(supports/challenges)、
    risk→thesis(threatens)。焦点为 thesis，端点经入向关系被展开。
    """

    service, repository = _service()
    base = datetime(2026, 6, 1, 9, tzinfo=UTC)
    thesis = _record(
        subject="example-co-demand",
        content="示例公司企业需求将持续增长",
        observed_at=base,
    )
    evidence1 = _record(
        subject="example-co-q2-beat",
        content="Q2 营收超预期，验证需求韧性",
        memory_type="evidence_claim",
        observed_at=base + timedelta(days=10),
    )
    risk1 = _record(
        subject="example-co-competition",
        content="竞品进入企业市场威胁份额",
        memory_type="risk",
        observed_at=base + timedelta(days=20),
    )
    challenge1 = _record(
        subject="example-co-margin-pressure",
        content="价格战压制毛利率",
        memory_type="evidence_claim",
        observed_at=base + timedelta(days=5),
    )
    for record in (thesis, evidence1, risk1, challenge1):
        repository.add(_PRINCIPAL, record)
    _link(repository, _manual_relation(source=evidence1, target=thesis, relation_type="supports", created_at=base))
    _link(repository, _manual_relation(source=risk1, target=thesis, relation_type="threatens", created_at=base))
    _link(repository, _manual_relation(source=challenge1, target=thesis, relation_type="challenges", created_at=base))

    result = service.recall_timeline(
        _PRINCIPAL,
        TimelineQuery(
            profile_id="investment-research",
            focus_memory_id=thesis.item.memory_id,
            max_hops=10,
        ),
    )
    assert result.focus is not None
    assert result.focus.memory_id == thesis.item.memory_id
    assert len(result.hops) == 3
    observed_times = [hop.memory.observed_at for hop in result.hops]
    assert observed_times == sorted(observed_times), "hops 应按 observed_at 升序"
    assert observed_times[0] == challenge1.current_revision.observed_at
    assert observed_times[-1] == risk1.current_revision.observed_at
    relation_types = {hop.relation_type for hop in result.hops}
    assert relation_types == {"supports", "threatens", "challenges"}


def test_timeline_cycle_does_not_revisit_endpoints() -> None:
    """两条 evidence_claim 同时 supports/challenges 同一 thesis，各自只出现一次。"""

    service, repository = _service()
    base = datetime(2026, 6, 1, 9, tzinfo=UTC)
    thesis = _record(
        subject="cycle-thesis",
        content="环防护测试论点",
        observed_at=base,
    )
    evidence_a = _record(
        subject="cycle-evidence-a",
        content="环防护测试证据 A",
        memory_type="evidence_claim",
        observed_at=base + timedelta(days=1),
    )
    evidence_b = _record(
        subject="cycle-evidence-b",
        content="环防护测试证据 B",
        memory_type="evidence_claim",
        observed_at=base + timedelta(days=2),
    )
    repository.add(_PRINCIPAL, thesis)
    repository.add(_PRINCIPAL, evidence_a)
    repository.add(_PRINCIPAL, evidence_b)
    # A supports thesis，B challenges thesis：两条入向关系端点不同，各自入列一次。
    _link(repository, _manual_relation(source=evidence_a, target=thesis, relation_type="supports", created_at=base))
    _link(repository, _manual_relation(source=evidence_b, target=thesis, relation_type="challenges", created_at=base))

    result = service.recall_timeline(
        _PRINCIPAL,
        TimelineQuery(
            profile_id="investment-research",
            focus_memory_id=thesis.item.memory_id,
            max_hops=10,
        ),
    )
    hop_ids = [hop.memory.memory_id for hop in result.hops]
    assert len(hop_ids) == 2
    assert evidence_a.item.memory_id in hop_ids
    assert evidence_b.item.memory_id in hop_ids
    assert thesis.item.memory_id not in hop_ids


def test_timeline_returns_empty_when_focus_missing() -> None:
    """focus 不存在时返回空结果（focus=None，hops 为空）。"""

    service, _repository = _service()
    result = service.recall_timeline(
        _PRINCIPAL,
        TimelineQuery(
            profile_id="investment-research",
            focus_memory_id=uuid4(),
            max_hops=5,
        ),
    )
    assert result.focus is None
    assert result.hops == ()
    assert "No timeline evolution" in result.rendered_context


def test_timeline_returns_empty_when_profile_has_no_timeline_relations() -> None:
    """general-work 未声明 timeline_relation_types，时间线返回空。"""

    repository = InMemoryMemoryRepository()
    service = MemoryService(
        repository,
        ProfileRegistry(),
        sensitive_guard=RegexSensitiveContentGuard(),
        clock=lambda: _NOW,
    )
    service.register_profile(GeneralWorkProfile())
    memory_id = uuid4()
    revision_id = uuid4()
    base = datetime(2026, 6, 1, 9, tzinfo=UTC)
    record = MemoryRecord(
        item=MemoryItem(
            memory_id=memory_id,
            owner_id="owner-a",
            profile_id="general-work",
            subject="generic-note",
            memory_type="preference",
            created_at=base,
        ),
        current_revision=MemoryRevision(
            revision_id=revision_id,
            memory_id=memory_id,
            owner_id="owner-a",
            revision_number=1,
            content="通用偏好",
            assertion_kind=AssertionKind.USER_VIEW,
            lifecycle_status=LifecycleStatus.ACTIVE,
            business_progress="monitoring",
            save_rationale="通用",
            observed_at=base,
            created_at=base,
            extraction_confidence=0.9,
            verification_status=VerificationStatus.USER_ASSERTED,
            sensitivity_level=SensitivityLevel.CONFIDENTIAL,
            valid_from=base,
            valid_until=_VALID_UNTIL,
        ),
        evidence=(
            Evidence(
                evidence_id=uuid4(),
                memory_id=memory_id,
                revision_id=revision_id,
                owner_id="owner-a",
                source_turn_id="turn-1",
                source_expression="通用偏好",
                observed_at=base,
                created_at=base,
                source_role=MessageRole.USER,
                source_type=EvidenceSourceType.CONVERSATION,
            ),
        ),
    )
    repository.add(_PRINCIPAL, record)
    result = service.recall_timeline(
        _PRINCIPAL,
        TimelineQuery(
            profile_id="general-work",
            focus_memory_id=memory_id,
            max_hops=5,
        ),
    )
    assert result.focus is None
    assert result.hops == ()


def test_timeline_truncates_to_max_hops() -> None:
    """max_hops=1 时只返回一跳，后续端点被截断。"""

    service, repository = _service()
    base = datetime(2026, 6, 1, 9, tzinfo=UTC)
    thesis = _record(
        subject="trunc-thesis",
        content="截断测试论点",
        observed_at=base,
    )
    evidence = _record(
        subject="trunc-evidence",
        content="截断测试证据",
        memory_type="evidence_claim",
        observed_at=base + timedelta(days=1),
    )
    risk = _record(
        subject="trunc-risk",
        content="截断测试风险",
        memory_type="risk",
        observed_at=base + timedelta(days=2),
    )
    for record in (thesis, evidence, risk):
        repository.add(_PRINCIPAL, record)
    _link(repository, _manual_relation(source=evidence, target=thesis, relation_type="supports", created_at=base))
    _link(repository, _manual_relation(source=risk, target=thesis, relation_type="threatens", created_at=base))

    result = service.recall_timeline(
        _PRINCIPAL,
        TimelineQuery(
            profile_id="investment-research",
            focus_memory_id=thesis.item.memory_id,
            max_hops=1,
        ),
    )
    assert len(result.hops) <= 1
