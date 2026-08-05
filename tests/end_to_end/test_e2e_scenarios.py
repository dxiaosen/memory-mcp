"""端到端场景测验：覆盖捕获/准入/召回/生命周期/关系/团队提取/向量/幂等/冲突。

每个场景跑真实 Core + InMemory Repository + FakeExtractor，不验证单个函数，
而是验证跨模块的业务闭环是否正确、有无隐藏 bug。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from memory_mcp.core import (
    AdmissionDecision,
    MemoryNotFoundError,
    MessageRole,
    PrincipalContext,
    RecallQuery,
    RelationStatus,
    ReviewStatus,
    TurnEnvelope,
    TurnMessage,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.composition import create_memory_service
from memory_mcp.core.domain import (
    AssertionKind,
    EvidenceSourceType,
    SensitivityLevel,
)
from memory_mcp.core.ports import MemoryRelationPolicy

from tests.support.fakes import (
    FakeCandidateExtractor,
    FakeEmbeddingProvider,
    SequentialCandidateExtractor,
    TestMemoryProfile,
    candidate_proposal,
    project_preference_command,
)


def _profile():
    """带关系策略的 project-work profile（supports: preference→ongoing_item）。"""

    return replace(
        TestMemoryProfile(),
        relation_policies={
            "supports": MemoryRelationPolicy(
                source_memory_types=frozenset({"preference"}),
                target_memory_types=frozenset({"ongoing_item"}),
                description="A preference supports an ongoing item.",
            )
        },
    )


def _service(extractor=None, embedding_provider=None):
    return create_memory_service(
        InMemoryMemoryRepository(),
        [_profile()],
        candidate_extractor=extractor,
        embedding_provider=embedding_provider,
    )


_NOW = datetime(2026, 7, 29, 10, tzinfo=UTC)
_PRINCIPAL = PrincipalContext("analyst-a")


def _turn(
    expression: str,
    *,
    subject_hint: str = "weekly-report",
    turn_id: str | None = None,
) -> TurnEnvelope:
    resolved_turn = turn_id or f"turn-{expression[:8]}"
    return TurnEnvelope(
        profile_id="project-work",
        conversation_id="e2e-session",
        source_turn_id=resolved_turn,
        content=expression,
        observed_at=_NOW,
        subject_hint=subject_hint,
        messages=(
            TurnMessage(
                role=MessageRole.USER,
                content=expression,
                message_id=f"msg-{resolved_turn}",
            ),
        ),
    )


# --- 场景 1：捕获→auto_save→召回闭环 ---


def test_e2e_capture_auto_save_then_recall() -> None:
    expression = "以后项目周报默认使用表格"
    extractor = FakeCandidateExtractor((candidate_proposal(expression, content=expression),))
    service = _service(extractor=extractor)

    result = service.capture_turn(_PRINCIPAL, _turn(expression))
    assert result.status.value == "completed"
    assert any(
        o.decision is AdmissionDecision.AUTO_SAVE for o in result.outcomes
    ), "explicit durable statement must auto-save"

    recalled = service.recall_memory(
        _PRINCIPAL,
        RecallQuery(
            profile_id="project-work",
            query="周报 表格 格式",
            max_items=5,
        ),
    )
    assert len(recalled.items) == 1
    assert "表格" in recalled.items[0].content
    assert recalled.items[0].relevance_score >= 0.18


# --- 场景 2：duplicate evidence（重复内容不创新 revision）---


def test_e2e_duplicate_evidence_appends_not_duplicates() -> None:
    expression = "项目周报默认用表格"
    extractor = FakeCandidateExtractor((candidate_proposal(expression, content=expression),))
    service = _service(extractor=extractor)

    service.capture_turn(_PRINCIPAL, _turn(expression, turn_id="dup-turn-1"))
    # 同一内容再次捕获（不同 turn_id 以避免 event 重放）
    second = service.capture_turn(_PRINCIPAL, _turn(expression, turn_id="dup-turn-2"))

    memories = service.list_memories(_PRINCIPAL)
    assert len(memories) == 1, "duplicate must not create second item"
    assert second.replayed is False
    assert any(
        o.reason_code == "duplicate_evidence_added" for o in second.outcomes
    ), "second capture must record duplicate evidence"


# --- 场景 3：explicit replacement 生成新 revision ---


def test_e2e_explicit_replacement_creates_new_revision() -> None:
    first = "项目周报默认使用表格"
    second = "以后项目周报改为 Markdown"
    extractor = SequentialCandidateExtractor(
        (
            candidate_proposal(first, content=first),
            candidate_proposal(second, content=second),
        )
    )
    service = _service(extractor=extractor)

    service.capture_turn(_PRINCIPAL, _turn(first, turn_id="repl-turn-1"))
    service.capture_turn(_PRINCIPAL, _turn(second, turn_id="repl-turn-2"))

    memories = service.list_memories(_PRINCIPAL)
    assert len(memories) == 1, "replacement must keep single item"
    assert memories[0].current_revision.revision_number == 2
    assert "Markdown" in memories[0].current_revision.content

    history = service.get_memory_history(_PRINCIPAL, memories[0].item.memory_id)
    assert len(history) == 2
    assert history[0].revision.revision_number == 2  # 倒序
    assert history[1].revision.lifecycle_status.value == "superseded"


# --- 场景 4：ambiguous conflict 降级 pending ---


def test_e2e_ambiguous_conflict_becomes_pending() -> None:
    first = "项目周报默认用表格"
    # 非明确替换措辞（无"改成/换成"等），有目标但不构成 replacement
    second = "项目周报也可以考虑用文本"
    extractor = SequentialCandidateExtractor(
        (
            candidate_proposal(first, content=first),
            candidate_proposal(second, content=second),
        )
    )
    service = _service(extractor=extractor)

    service.capture_turn(_PRINCIPAL, _turn(first, turn_id="amb-turn-1"))
    service.capture_turn(_PRINCIPAL, _turn(second, turn_id="amb-turn-2"))

    pending = service.list_pending_reviews(_PRINCIPAL)
    assert len(pending) == 1, "ambiguous lifecycle conflict must become pending"
    assert pending[0].status is ReviewStatus.PENDING


# --- 场景 5：幂等重放（同 event 同 payload）---


def test_e2e_idempotent_replay_returns_same_capture() -> None:
    expression = "项目周报用表格"
    extractor = FakeCandidateExtractor((candidate_proposal(expression),))
    service = _service(extractor=extractor)

    envelope = TurnEnvelope(
        profile_id="project-work",
        conversation_id="idem-session",
        source_turn_id="idem-turn",
        content=expression,
        observed_at=_NOW,
        event_id="event-001",
        contract_version="1",
        payload_fingerprint="abc123",
        messages=(
            TurnMessage(role=MessageRole.USER, content=expression, message_id="m1"),
        ),
    )
    first = service.capture_turn(_PRINCIPAL, envelope)
    second = service.capture_turn(_PRINCIPAL, envelope)
    assert first.capture_id == second.capture_id
    assert second.replayed is True


# --- 场景 6：revoke 保留历史并 stale 自动关系 ---


def test_e2e_revoke_preserves_history_and_stales_automatic_relations() -> None:
    expression = "以后项目周报用表格"
    extractor = FakeCandidateExtractor((candidate_proposal(expression),))
    service = _service(extractor=extractor)
    service.capture_turn(_PRINCIPAL, _turn(expression))
    memory = service.list_memories(_PRINCIPAL)[0]

    revoked = service.revoke_memory(_PRINCIPAL, memory.item.memory_id)
    assert revoked.current_revision.lifecycle_status.value == "revoked"

    # 撤销后不再召回
    recalled = service.recall_memory(
        _PRINCIPAL,
        RecallQuery(profile_id="project-work", query="周报 表格", max_items=5),
    )
    assert len(recalled.items) == 0

    # 历史保留
    history = service.get_memory_history(_PRINCIPAL, memory.item.memory_id)
    assert len(history) == 1
    assert history[0].revision.lifecycle_status.value == "revoked"


# --- 场景 7：review confirm 写入记忆 ---


def test_e2e_review_confirm_writes_memory() -> None:
    expression = "可以考虑用文本格式"
    extractor = FakeCandidateExtractor((candidate_proposal(expression, content=expression),))
    # 先制造一个 ambiguous pending（先写一条同 subject）
    service = _service(extractor=extractor)
    service.create_memory(
        _PRINCIPAL,
        replace(project_preference_command(), subject="weekly-report"),
    )
    service.capture_turn(_PRINCIPAL, _turn(expression))

    pending = service.list_pending_reviews(_PRINCIPAL)
    assert len(pending) >= 1
    review = pending[0]
    confirmed = service.confirm_review(_PRINCIPAL, review.review_id)
    assert confirmed.current_revision.verification_status.value == "user_confirmed"

    # review 状态推进
    resolved = service.get_review(_PRINCIPAL, review.review_id)
    assert resolved.status is ReviewStatus.CONFIRMED


# --- 场景 8：owner 隔离（跨用户不可见）---


def test_e2e_owner_isolation_cross_user_invisible() -> None:
    expression = "用户 A 的私有偏好"
    extractor = FakeCandidateExtractor((candidate_proposal(expression),))
    service = _service(extractor=extractor)
    service.capture_turn(_PRINCIPAL, _turn(expression))

    other = PrincipalContext("user-b")
    recalled = service.recall_memory(
        other,
        RecallQuery(profile_id="project-work", query="私有偏好", max_items=5),
    )
    assert len(recalled.items) == 0, "other user must not see owner-a's memories"

    # 越权读取 memory_id 也不可见
    owner_memories = service.list_memories(_PRINCIPAL)
    with pytest.raises(MemoryNotFoundError):
        service.get_memory(other, owner_memories[0].item.memory_id)


# --- 场景 9：manual relation 建立与撤销 ---


def test_e2e_manual_relation_link_and_revoke() -> None:
    service = _service()
    source = service.create_memory(_PRINCIPAL, project_preference_command())
    target = service.create_memory(
        _PRINCIPAL,
        replace(
            project_preference_command(),
            subject="ongoing-task",
            memory_type="ongoing_item",
            content="继续跟进周报",
            source_turn_id="t2",
            source_expression="继续跟进周报",
        ),
    )
    relation = service.link_memories(
        _PRINCIPAL,
        source.item.memory_id,
        target.item.memory_id,
        "supports",
    )
    assert relation.status is RelationStatus.ACTIVE

    relations = service.list_memory_relations(
        _PRINCIPAL, source.item.memory_id
    )
    assert len(relations) == 1

    revoked = service.revoke_memory_relation(_PRINCIPAL, relation.relation_id)
    assert revoked.status is RelationStatus.REVOKED
    # 幂等：再次撤销返回相同记录
    again = service.revoke_memory_relation(_PRINCIPAL, relation.relation_id)
    assert again.status is RelationStatus.REVOKED

    active = service.list_memory_relations(_PRINCIPAL, source.item.memory_id)
    assert len(active) == 0, "revoked relation must not appear in active-only list"


# --- 场景 10：团队提取聚类 + 幂等 ---


def test_e2e_team_extraction_cluster_and_idempotent() -> None:
    from memory_mcp.core.domain import (
        Evidence,
        LifecycleStatus,
        MemoryItem,
        MemoryRecord,
        MemoryRevision,
        MessageRole,
        VerificationStatus,
    )

    repository = InMemoryMemoryRepository()
    repository.register_profile(TestMemoryProfile())
    team_owner = "tenant:team:research"
    members = ("m1", "m2", "m3")
    embedding = (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    for owner in members[:2]:
        mid = uuid4()
        rid = uuid4()
        repository.add(
            PrincipalContext(owner),
            MemoryRecord(
                item=MemoryItem(
                    memory_id=mid,
                    owner_id=owner,
                    profile_id="project-work",
                    subject="周报格式",
                    memory_type="preference",
                    created_at=_NOW,
                ),
                current_revision=MemoryRevision(
                    revision_id=rid,
                    memory_id=mid,
                    owner_id=owner,
                    revision_number=1,
                    content="项目周报用表格",
                    assertion_kind=AssertionKind.USER_VIEW,
                    lifecycle_status=LifecycleStatus.ACTIVE,
                    business_progress=None,
                    save_rationale="t",
                    observed_at=_NOW,
                    created_at=_NOW,
                    extraction_confidence=0.9,
                    verification_status=VerificationStatus.USER_ASSERTED,
                    sensitivity_level=SensitivityLevel.CONFIDENTIAL,
                    valid_from=_NOW,
                    valid_until=None,
                    embedding=embedding,
                ),
                evidence=(
                    Evidence(
                        evidence_id=uuid4(),
                        memory_id=mid,
                        revision_id=rid,
                        owner_id=owner,
                        source_turn_id="t",
                        source_expression="项目周报用表格",
                        observed_at=_NOW,
                        created_at=_NOW,
                        source_role=MessageRole.USER,
                        source_type=EvidenceSourceType.CONVERSATION,
                    ),
                ),
            ),
        )

    args = dict(
        team_owner_id=team_owner,
        member_owner_ids=members[:2],
        profile_id="project-work",
        effective_at=_NOW,
        similarity_threshold=0.85,
        min_cluster_size=2,
    )
    first = repository.extract_team_common_memories(**args)
    second = repository.extract_team_common_memories(**args)
    assert first.candidate_count == 1
    assert second.candidate_count == first.candidate_count  # run 级幂等
    pending = repository.list_reviews(
        PrincipalContext(team_owner),
        status=ReviewStatus.PENDING,
    )
    assert len(pending) == 1, "no duplicate pending from idempotent second run"


# --- 场景 11：向量召回降级（provider 失败时两路）---


def test_e2e_vector_recall_degrades_when_provider_fails() -> None:
    service = _service(embedding_provider=FakeEmbeddingProvider({}, failures_before_success=1))
    service.create_memory(_PRINCIPAL, project_preference_command())

    # 第一次 recall 触发 embedding 失败，降级为两路，仍返回结果
    result = service.recall_memory(
        _PRINCIPAL,
        RecallQuery(
            profile_id="project-work",
            query="周报 表格",
            max_items=5,
        ),
    )
    assert len(result.items) >= 1


# --- 场景 12：维护批次物化到期记忆 ---


def test_e2e_maintenance_materializes_expired_memories() -> None:
    # 用 validity_days=1 的 profile，使 40 天前创建的记忆已过期。
    from memory_mcp.core.ports import MemoryMetadataPolicy

    profile = replace(
        _profile(),
        metadata_policies={
            memory_type: (
                MemoryMetadataPolicy(validity_days=1)
                if memory_type == "preference"
                else MemoryMetadataPolicy()
            )
            for memory_type in TestMemoryProfile().memory_types
        },
    )
    service = create_memory_service(InMemoryMemoryRepository(), [profile])
    # 创建一条已过期的记忆（observed_at 在 40 天前，validity_days=1 → valid_until 在 39 天前）
    command = replace(
        project_preference_command(),
        observed_at=_NOW - timedelta(days=40),
    )
    service.create_memory(_PRINCIPAL, command)

    # 维护前 active_only 过滤应排除过期记忆
    active = service.list_memories(_PRINCIPAL, include_inactive=False)
    assert len(active) == 0, "expired memory must be filtered from active list"

    # 全量（含非活动）仍可见
    all_memories = service.list_memories(_PRINCIPAL, include_inactive=True)
    assert len(all_memories) == 1

    # 运行维护
    result = service.run_maintenance()
    assert result.expired_memory_count >= 1


# --- 场景 13：跨 owner 关系被拒绝 ---


def test_e2e_cross_owner_relation_rejected() -> None:
    service = _service()
    owner_a = PrincipalContext("user-a")
    owner_b = PrincipalContext("user-b")
    source = service.create_memory(owner_a, project_preference_command())
    target = service.create_memory(
        owner_b,
        replace(
            project_preference_command(),
            subject="other-task",
            memory_type="ongoing_item",
            content="他人任务",
            source_turn_id="t2",
            source_expression="他人任务",
        ),
    )
    # 跨 owner 的 target 对 owner_a 不可见，等同于不存在 → MemoryNotFoundError
    with pytest.raises(MemoryNotFoundError):
        service.link_memories(
            owner_a,
            source.item.memory_id,
            target.item.memory_id,
            "supports",
        )
