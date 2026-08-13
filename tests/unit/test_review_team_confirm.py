"""团队 review 确认（含团队提升）的契约测试。

覆盖生产漏洞：team extraction 产出的团队候选（candidate.owner = team owner）被
成员 confirm 时，若成员个人已有同 subject+type 的记忆，``review_service.confirm``
构造的 ``lookup_principal.visible_owner_ids`` 仍含个人 owner，``find_current`` 误
命中个人记忆 -> 走 replacement 关联到个人记忆，团队记忆没在 team owner 下落地。
"""

from __future__ import annotations

from datetime import UTC, datetime

from memory_mcp.core import (
    AssertionKind,
    MemoryService,
    MessageRole,
    PrincipalContext,
    TurnEnvelope,
    TurnMessage,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.composition import create_memory_service
from memory_mcp.profiles import InvestmentResearchProfile

from tests.support.fakes import (
    FakeCandidateExtractor,
    candidate_proposal,
)

_TEAM_OWNER = "tenant-001:team:research-dept"
_MEMBER_A = PrincipalContext(
    "tenant-001:subject-001",
    (_TEAM_OWNER,),
)
_MEMBER_B = PrincipalContext(
    "tenant-001:subject-002",
    (_TEAM_OWNER,),
)
_NOW = datetime(2026, 8, 1, 10, tzinfo=UTC)


def _turn(text: str, *, owner: PrincipalContext, turn_id: str) -> TurnEnvelope:
    return TurnEnvelope(
        profile_id="investment-research",
        conversation_id=f"conv-{owner.owner_id}",
        source_turn_id=turn_id,
        content=text,
        observed_at=_NOW,
        subject_hint="popmart",
        messages=(
            TurnMessage(
                role=MessageRole.USER,
                content=text,
                message_id=f"message-{turn_id}",
            ),
        ),
    )


def _service(vectors: dict[str, tuple[float, ...]]) -> tuple[
    MemoryService, FakeCandidateExtractor
]:
    extractor = FakeCandidateExtractor(())
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [InvestmentResearchProfile()],
        candidate_extractor=extractor,
        embedding_provider=_FakeProvider(vectors),
    )
    return service, extractor


class _FakeProvider:
    model_id = "fake-embedding-model"
    dimensions = 8

    def __init__(self, vectors: dict[str, tuple[float, ...]]) -> None:
        self._vectors = dict(vectors)

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        zero = tuple(0.0 for _ in range(self.dimensions))
        return tuple(self._vectors.get(text, zero) for text in texts)


def test_team_review_confirm_lands_in_team_owner_not_personal() -> None:
    """团队候选被成员 confirm 后，记忆应落在 team owner，而非成员个人 owner。

    复现生产漏洞：成员 B 个人已有同 subject+type 的 thesis，confirm 团队候选时
    ``find_current`` 误命中 B 的个人记忆，导致团队候选关联到个人、team owner 下
    无记忆产出，其他成员召回时看不到团队共识。
    """

    shared_subject = "popmart-oversea-thesis"
    member_a_content = "我认为泡泡玛特海外增长可持续"
    # 成员 B 的个人记忆与团队候选 subject 相同（触发误命中的前提）。
    member_b_content = "我认为泡泡玛特海外增长是结构性的"
    # team extraction 聚类后的 subject/content 由簇内频次选择，这里用共享 subject。
    vectors = {
        # 两成员判断语义高度相似 -> 聚成一簇。
        member_a_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        member_b_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    service, extractor = _service(vectors)

    # 1) 成员 A 建个人 thesis（owner=subject-001）。
    extractor.proposals = (
        candidate_proposal(
            member_a_content,
            subject=shared_subject,
            memory_type="thesis",
            content=member_a_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    service.capture_turn(_MEMBER_A, _turn(member_a_content, owner=_MEMBER_A, turn_id="ta1"))

    # 2) 成员 B 建个人 thesis（owner=subject-002，不同 owner 命名空间，不互相 replacement）。
    extractor.proposals = (
        candidate_proposal(
            member_b_content,
            subject=shared_subject,
            memory_type="thesis",
            content=member_b_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    service.capture_turn(_MEMBER_B, _turn(member_b_content, owner=_MEMBER_B, turn_id="tb1"))

    # 两人各有一条个人 active 记忆。
    a_memories = service.list_memories(_MEMBER_A)
    b_memories = service.list_memories(_MEMBER_B)
    assert len(a_memories) == 1
    assert len(b_memories) == 1

    # 3) team extraction 聚类 -> 产出团队 pending review（candidate.owner=team owner）。
    repository = service._capture_service._repository  # type: ignore[attr-defined]
    result = repository.extract_team_common_memories(
        team_owner_id=_TEAM_OWNER,
        member_owner_ids=(_MEMBER_A.owner_id, _MEMBER_B.owner_id),
        profile_id="investment-research",
        effective_at=_NOW,
        similarity_threshold=0.70,
        min_cluster_size=2,
    )
    assert result.cluster_count == 1
    assert result.candidate_count == 1

    team_reviews = [
        r
        for r in service.list_pending_reviews(_MEMBER_B)
        if r.candidate.owner_id == _TEAM_OWNER
    ]
    assert len(team_reviews) == 1
    team_review = team_reviews[0]

    # 4) 成员 B confirm 这条团队候选（不传 team_id：candidate 已是 team owner，
    #    review_service 应以 candidate owner 为 target 在 team owner 下落地）。
    memory = service.confirm_review(
        _MEMBER_B,
        team_review.review_id,
        team_id=None,
        team_owner_ids=frozenset({_TEAM_OWNER}),
    )

    # 团队记忆应落在 team owner，而非成员 B 的个人 owner。
    assert memory.item.owner_id == _TEAM_OWNER, (
        f"团队候选 confirm 后应写入 team owner={_TEAM_OWNER}，"
        f"实际写入 {memory.item.owner_id}（误命中成员 B 个人记忆）"
    )

    # team owner 下应有该记忆，成员 A 召回时能看到团队共识。
    team_memories = [
        m for m in service.list_memories(_MEMBER_A) if m.item.owner_id == _TEAM_OWNER
    ]
    assert len(team_memories) == 1, "成员应能召回 team owner 下的团队共识记忆"


def test_team_extraction_reproduces_after_team_memory_revoked(
    service: MemoryService | None = None,
    extractor: FakeCandidateExtractor | None = None,
    vectors: dict[str, tuple[float, ...]] | None = None,
) -> None:
    """团队记忆被 revoke 后，team extraction 应能再次产出相同判断的候选。

    复现生产漏洞：confirm 团队候选 -> revoke 该团队记忆 -> 再次 team extraction。
    幂等检查查 ``memory_reviews`` 的 ``status IN ('pending','confirmed')``，
    但 revoke 不动 review 表，旧 confirmed review 一直挡在幂等检查前 ->
    ``candidate_count`` 永远为 0，相同判断的团队共识无法重建。

    修复后：confirmed review 指向的 memory 已 revoked 时，幂等检查不再视为
    "已存在"，team extraction 能为相同判断产出新候选。
    """

    shared_subject = "popmart-oversea-platform-thesis"
    member_a_content = "我认为泡泡玛特平台化能力已验证"
    member_b_content = "我认为泡泡玛特已成多 IP 平台"
    vecs = {
        member_a_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        member_b_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    svc, ext = _service(vecs)
    # 第二次 team extraction 用更晚的 effective_at 绕过 run 级幂等缓存。
    later = datetime(2026, 8, 1, 11, tzinfo=UTC)

    # 两成员各建同 subject+type 的个人 thesis。
    ext.proposals = (
        candidate_proposal(
            member_a_content,
            subject=shared_subject,
            memory_type="thesis",
            content=member_a_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    svc.capture_turn(_MEMBER_A, _turn(member_a_content, owner=_MEMBER_A, turn_id="ra1"))
    ext.proposals = (
        candidate_proposal(
            member_b_content,
            subject=shared_subject,
            memory_type="thesis",
            content=member_b_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    svc.capture_turn(_MEMBER_B, _turn(member_b_content, owner=_MEMBER_B, turn_id="rb1"))

    # 第一次 team extraction -> 产出团队 pending review。
    repository = svc._capture_service._repository  # type: ignore[attr-defined]
    result1 = repository.extract_team_common_memories(
        team_owner_id=_TEAM_OWNER,
        member_owner_ids=(_MEMBER_A.owner_id, _MEMBER_B.owner_id),
        profile_id="investment-research",
        effective_at=_NOW,
        similarity_threshold=0.70,
        min_cluster_size=2,
    )
    assert result1.candidate_count == 1, "首次提取应产出 1 条团队候选"

    # confirm 这条团队候选 -> team owner 下建 active 记忆。
    team_review = next(
        r
        for r in svc.list_pending_reviews(_MEMBER_B)
        if r.candidate.owner_id == _TEAM_OWNER
    )
    memory = svc.confirm_review(
        _MEMBER_B,
        team_review.review_id,
        team_id=None,
        team_owner_ids=frozenset({_TEAM_OWNER}),
    )
    assert memory.item.owner_id == _TEAM_OWNER

    # revoke 这条团队记忆。
    revoked = svc.revoke_memory(_MEMBER_B, memory.item.memory_id)
    assert revoked is not None
    assert revoked.current_revision.lifecycle_status.value == "revoked"

    # 再次 team extraction：旧 confirmed review 指向的 memory 已 revoked，
    # 幂等检查不应挡住 -> 应再次产出新候选。
    result2 = repository.extract_team_common_memories(
        team_owner_id=_TEAM_OWNER,
        member_owner_ids=(_MEMBER_A.owner_id, _MEMBER_B.owner_id),
        profile_id="investment-research",
        effective_at=later,
        similarity_threshold=0.70,
        min_cluster_size=2,
    )
    assert result2.candidate_count == 1, (
        "团队记忆被 revoke 后，相同判断的团队候选应能再次产出；"
        "旧 confirmed review 指向已 revoked memory 时不应挡住幂等检查"
    )

    # 新候选应能被 confirm 成新 active 记忆（唯一索引槽位已释放）。
    new_team_reviews = [
        r
        for r in svc.list_pending_reviews(_MEMBER_B)
        if r.candidate.owner_id == _TEAM_OWNER and r.status.value == "pending"
    ]
    assert len(new_team_reviews) == 1, "应有 1 条新团队 pending review"
    new_memory = svc.confirm_review(
        _MEMBER_B,
        new_team_reviews[0].review_id,
        team_id=None,
        team_owner_ids=frozenset({_TEAM_OWNER}),
    )
    assert new_memory.item.owner_id == _TEAM_OWNER
    assert new_memory.current_revision.lifecycle_status.value == "active"
    assert new_memory.item.memory_id != memory.item.memory_id
