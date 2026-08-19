"""团队公共记忆自动提取的契约测试（InMemory 适配器）。

覆盖：
- 相似成员记忆聚成一个候选并写入团队 pending review；
- 同 subject+type 的已有 pending 不重复创建（幂等）；
- 无 embedding 的成员记忆不参与聚类；
- 非成员个人记忆不可见；
- 相似度阈值未达或簇小于最小值时不产出候选。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from memory_mcp.core import PrincipalContext, ReviewStatus, TeamExtractionResult
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
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
    VerificationStatus,
)

from tests.support.fakes import TestMemoryProfile

_NOW = datetime(2026, 7, 29, 10, tzinfo=UTC)
_TEAM_OWNER = "tenant-a:team:research"
_MEMBERS = ("member-x", "member-y", "member-z")
_EMBEDDING_SEMANTIC_A = (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
_EMBEDDING_SEMANTIC_A_DRIFT = (0.95, 0.05, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
_EMBEDDING_SEMANTIC_B = (0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def _member_record(
    *,
    owner_id: str,
    subject: str,
    content: str,
    embedding: tuple[float, ...],
    memory_type: str = "preference",
    business_progress: str | None = None,
) -> MemoryRecord:
    """构造一条带 embedding 的成员个人活动记忆。"""

    memory_id = uuid4()
    revision_id = uuid4()
    return MemoryRecord(
        item=MemoryItem(
            memory_id=memory_id,
            owner_id=owner_id,
            profile_id="project-work",
            subject=subject,
            memory_type=memory_type,
            created_at=_NOW,
        ),
        current_revision=MemoryRevision(
            revision_id=revision_id,
            memory_id=memory_id,
            owner_id=owner_id,
            revision_number=1,
            content=content,
            assertion_kind=AssertionKind.USER_VIEW,
            lifecycle_status=LifecycleStatus.ACTIVE,
            business_progress=business_progress,
            save_rationale="成员个人记忆",
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
                memory_id=memory_id,
                revision_id=revision_id,
                owner_id=owner_id,
                source_turn_id=f"{owner_id}-turn-1",
                source_expression=content,
                observed_at=_NOW,
                created_at=_NOW,
                source_role=MessageRole.USER,
                source_type=EvidenceSourceType.CONVERSATION,
            ),
        ),
    )


def _repo_with_members() -> InMemoryMemoryRepository:
    """构造已注册 profile 的 in-memory 仓库。"""

    repository = InMemoryMemoryRepository()
    repository.register_profile(TestMemoryProfile())
    return repository


def test_similar_member_memories_cluster_into_team_pending() -> None:
    """两个成员写了语义相似的偏好，聚类后产出一条团队 pending。"""

    repository = _repo_with_members()
    for owner in _MEMBERS[:2]:
        repository.add(
            PrincipalContext(owner),
            _member_record(
                owner_id=owner,
                subject="周报格式",
                content="项目周报用表格",
                embedding=_EMBEDDING_SEMANTIC_A,
            ),
        )
    result = repository.extract_team_common_memories(
        team_owner_id=_TEAM_OWNER,
        member_owner_ids=_MEMBERS[:2],
        profile_id="project-work",
        effective_at=_NOW,
        similarity_threshold=0.85,
        min_cluster_size=2,
    )
    assert isinstance(result, TeamExtractionResult)
    assert result.member_count == 2
    assert result.memory_count == 2
    assert result.cluster_count == 1
    assert result.candidate_count == 1
    pending = repository.list_reviews(
        PrincipalContext(_TEAM_OWNER),
        status=ReviewStatus.PENDING,
    )
    assert len(pending) == 1
    review = pending[0]
    assert review.owner_id == _TEAM_OWNER
    assert review.candidate.owner_id == _TEAM_OWNER
    assert review.candidate.subject == "周报格式"


def test_idempotent_second_run_does_not_duplicate_pending() -> None:
    """同一 (team, profile, effective_at) 的第二次提取直接返回既有计数，不重复扫描/创建。"""

    repository = _repo_with_members()
    for owner in _MEMBERS[:2]:
        repository.add(
            PrincipalContext(owner),
            _member_record(
                owner_id=owner,
                subject="周报格式",
                content="项目周报用表格",
                embedding=_EMBEDDING_SEMANTIC_A,
            ),
        )
    args = dict(
        team_owner_id=_TEAM_OWNER,
        member_owner_ids=_MEMBERS[:2],
        profile_id="project-work",
        effective_at=_NOW,
        similarity_threshold=0.85,
        min_cluster_size=2,
    )
    first = repository.extract_team_common_memories(**args)
    second = repository.extract_team_common_memories(**args)
    # run 级幂等：第二次返回与第一次相同的计数（no-op，不重新扫描/聚类/写 pending）。
    assert first.candidate_count == 1
    assert second.candidate_count == first.candidate_count
    assert second.memory_count == first.memory_count
    assert (
        len(
            repository.list_reviews(
                PrincipalContext(_TEAM_OWNER),
                status=ReviewStatus.PENDING,
            ),
        )
        == 1
    )


def test_memories_without_embedding_are_excluded() -> None:
    """无 embedding 的成员记忆不参与聚类。"""

    repository = _repo_with_members()
    record_without_embedding = _member_record(
        owner_id=_MEMBERS[0],
        subject="周报格式",
        content="项目周报用表格",
        embedding=None,
    )
    repository.add(PrincipalContext(_MEMBERS[0]), record_without_embedding)
    result = repository.extract_team_common_memories(
        team_owner_id=_TEAM_OWNER,
        member_owner_ids=(_MEMBERS[0],),
        profile_id="project-work",
        effective_at=_NOW,
        similarity_threshold=0.85,
        min_cluster_size=1,
    )
    assert result.memory_count == 0
    assert result.candidate_count == 0


def test_non_member_personal_memory_not_clustered() -> None:
    """非成员的个人记忆即使与成员相似也不会进入团队提取。"""

    repository = _repo_with_members()
    repository.add(
        PrincipalContext(_MEMBERS[0]),
        _member_record(
            owner_id=_MEMBERS[0],
            subject="周报格式",
            content="项目周报用表格",
            embedding=_EMBEDDING_SEMANTIC_A,
        ),
    )
    repository.add(
        PrincipalContext(_MEMBERS[1]),
        _member_record(
            owner_id=_MEMBERS[1],
            subject="周报格式",
            content="项目周报用表格",
            embedding=_EMBEDDING_SEMANTIC_A,
        ),
    )
    # 外部人员写入相同内容，但不在 member_owner_ids 内
    repository.add(
        PrincipalContext("outsider"),
        _member_record(
            owner_id="outsider",
            subject="周报格式",
            content="项目周报用表格",
            embedding=_EMBEDDING_SEMANTIC_A,
        ),
    )
    result = repository.extract_team_common_memories(
        team_owner_id=_TEAM_OWNER,
        member_owner_ids=(_MEMBERS[0], _MEMBERS[1]),
        profile_id="project-work",
        effective_at=_NOW,
        similarity_threshold=0.85,
        min_cluster_size=2,
    )
    assert result.member_count == 2
    # 仅 2 个成员的记忆被扫描，外部人员记忆不计入 memory_count
    assert result.memory_count == 2
    assert result.candidate_count == 1


def test_single_member_echo_chamber_produces_no_candidate() -> None:
    """单个成员写多条近似记忆不构成团队共性（unique_owners < 2）。"""

    repository = _repo_with_members()
    repository.add(
        PrincipalContext(_MEMBERS[0]),
        _member_record(
            owner_id=_MEMBERS[0],
            subject="周报格式",
            content="项目周报用表格",
            embedding=_EMBEDDING_SEMANTIC_A,
        ),
    )
    repository.add(
        PrincipalContext(_MEMBERS[0]),
        _member_record(
            owner_id=_MEMBERS[0],
            subject="周报格式",
            content="项目周报还是用表格",
            embedding=_EMBEDDING_SEMANTIC_A_DRIFT,
        ),
    )
    result = repository.extract_team_common_memories(
        team_owner_id=_TEAM_OWNER,
        member_owner_ids=(_MEMBERS[0],),
        profile_id="project-work",
        effective_at=_NOW,
        similarity_threshold=0.85,
        min_cluster_size=2,
    )
    assert result.memory_count == 2
    # 同一成员的两条近似记忆聚成一簇，但 unique_owners=1 不产出团队候选
    assert result.candidate_count == 0


def test_below_similarity_threshold_produces_no_cluster() -> None:
    """相似度低于阈值的两条记忆不聚成簇，不产出候选。"""

    repository = _repo_with_members()
    repository.add(
        PrincipalContext(_MEMBERS[0]),
        _member_record(
            owner_id=_MEMBERS[0],
            subject="周报格式",
            content="项目周报用表格",
            embedding=_EMBEDDING_SEMANTIC_A,
        ),
    )
    repository.add(
        PrincipalContext(_MEMBERS[1]),
        _member_record(
            owner_id=_MEMBERS[1],
            subject="会议纪要",
            content="会议纪要用文本",
            embedding=_EMBEDDING_SEMANTIC_B,
        ),
    )
    result = repository.extract_team_common_memories(
        team_owner_id=_TEAM_OWNER,
        member_owner_ids=_MEMBERS[:2],
        profile_id="project-work",
        effective_at=_NOW,
        similarity_threshold=0.85,
        min_cluster_size=2,
    )
    assert result.memory_count == 2
    assert result.cluster_count == 0
    assert result.candidate_count == 0


def test_empty_member_list_returns_zero_result() -> None:
    """无成员时返回全零结果，不报错。"""

    repository = _repo_with_members()
    result = repository.extract_team_common_memories(
        team_owner_id=_TEAM_OWNER,
        member_owner_ids=(),
        profile_id="project-work",
        effective_at=_NOW,
        similarity_threshold=0.85,
        min_cluster_size=2,
    )
    assert result.member_count == 0
    assert result.candidate_count == 0


def test_team_extraction_service_run_once_collects_results() -> None:
    """``TeamExtractionService.run_once`` 对每个团队配置产出结果。"""

    from memory_mcp.core.application.team_extraction_service import (
        TeamExtractionService,
    )

    repository = _repo_with_members()
    repository.add(
        PrincipalContext(_MEMBERS[0]),
        _member_record(
            owner_id=_MEMBERS[0],
            subject="周报格式",
            content="项目周报用表格",
            embedding=_EMBEDDING_SEMANTIC_A,
        ),
    )
    repository.add(
        PrincipalContext(_MEMBERS[1]),
        _member_record(
            owner_id=_MEMBERS[1],
            subject="周报格式",
            content="项目周报用表格",
            embedding=_EMBEDDING_SEMANTIC_A_DRIFT,
        ),
    )
    service = TeamExtractionService(
        repository,
        _profile_registry_with_test_profile(),
        clock=lambda: _NOW,
        team_configs=((_TEAM_OWNER, _MEMBERS[:2], "project-work"),),
    )
    results = service.run_once()
    assert len(results) == 1
    assert results[0].team_owner_id == _TEAM_OWNER
    assert results[0].candidate_count == 1


def test_duplicate_team_owner_configs_are_deduped_with_member_union() -> None:
    """同一 team_owner_id 按成员重复配置时去重并合并成员。

    三条配置指向同一 team 但成员不同（模拟按成员展开产生同一 team owner）。去重后只跑一次，
    成员取并集，使两成员的相似记忆能满足 min_cluster_size=2 聚成一簇。
    """

    from memory_mcp.core.application.team_extraction_service import (
        TeamExtractionService,
    )

    repository = _repo_with_members()
    repository.add(
        PrincipalContext(_MEMBERS[1]),
        _member_record(
            owner_id=_MEMBERS[1],
            subject="周报格式",
            content="项目周报用表格",
            embedding=_EMBEDDING_SEMANTIC_A,
        ),
    )
    repository.add(
        PrincipalContext(_MEMBERS[2]),
        _member_record(
            owner_id=_MEMBERS[2],
            subject="周报格式",
            content="项目周报用表格",
            embedding=_EMBEDDING_SEMANTIC_A_DRIFT,
        ),
    )
    service = TeamExtractionService(
        repository,
        _profile_registry_with_test_profile(),
        clock=lambda: _NOW,
        team_configs=(
            (_TEAM_OWNER, (_MEMBERS[1],), "project-work"),
            (_TEAM_OWNER, (_MEMBERS[2],), "project-work"),
            (_TEAM_OWNER, (_MEMBERS[1], _MEMBERS[2]), "project-work"),
        ),
    )
    results = service.run_once()

    # 去重后同一 team 只跑一次（而非 team_count=3 重复同一 team_owner_ref）。
    assert len(results) == 1
    assert results[0].team_owner_id == _TEAM_OWNER
    # 成员取并集（member-y + member-z），两成员相似记忆聚成一簇 -> 1 candidate。
    # 若未合并成员（只保留首个配置的 member-y），min_cluster_size=2 不满足 -> 0 candidate。
    assert results[0].candidate_count == 1


def _profile_registry_with_test_profile():
    from memory_mcp.core.ports import ProfileRegistry

    from tests.support.fakes import TestMemoryProfile

    registry = ProfileRegistry()
    registry.register(TestMemoryProfile())
    return registry


# 两段语义正交的 embedding：主题归并测试里让 risk 与 thesis 的 embedding 相似度
# 低于阈值（不聚成 embedding 簇），仅靠 subject 关键词重叠归并。
def test_subject_tie_break_is_deterministic_across_runs() -> None:
    """两成员不同 subject 各一次，subject 字典序最小者胜出且两次运行一致。"""

    repository = _repo_with_members()
    for owner, subject in ((_MEMBERS[0], "周报格式B"), (_MEMBERS[1], "周报格式A")):
        repository.add(
            PrincipalContext(owner),
            _member_record(
                owner_id=owner,
                subject=subject,
                content="项目周报用表格",
                embedding=_EMBEDDING_SEMANTIC_A,
            ),
        )
    args = dict(
        team_owner_id=_TEAM_OWNER,
        member_owner_ids=_MEMBERS[:2],
        profile_id="project-work",
        effective_at=_NOW,
        similarity_threshold=0.85,
        min_cluster_size=2,
    )
    repository.extract_team_common_memories(**args)
    pending = repository.list_reviews(
        PrincipalContext(_TEAM_OWNER),
        status=ReviewStatus.PENDING,
    )
    assert len(pending) == 1
    # 字典序最小的 "周报格式A" 胜出，非依赖 set 哈希顺序。
    assert pending[0].candidate.subject == "周报格式A"


def test_divergence_rationale_preserves_minority_view() -> None:
    """簇内存在少数视角时 save_rationale 追加分歧摘要并引用少数成员 content。"""

    repository = _repo_with_members()
    # 主内容更长（被选为主表达），少数视角更短（被引用为分歧）。
    repository.add(
        PrincipalContext(_MEMBERS[0]),
        _member_record(
            owner_id=_MEMBERS[0],
            subject="周报格式",
            content="周报用 Markdown 列表并附变更说明",
            embedding=_EMBEDDING_SEMANTIC_A,
        ),
    )
    repository.add(
        PrincipalContext(_MEMBERS[1]),
        _member_record(
            owner_id=_MEMBERS[1],
            subject="周报格式",
            content="项目周报用表格",
            embedding=_EMBEDDING_SEMANTIC_A_DRIFT,
        ),
    )
    repository.extract_team_common_memories(
        team_owner_id=_TEAM_OWNER,
        member_owner_ids=_MEMBERS[:2],
        profile_id="project-work",
        effective_at=_NOW,
        similarity_threshold=0.85,
        min_cluster_size=2,
    )
    pending = repository.list_reviews(
        PrincipalContext(_TEAM_OWNER),
        status=ReviewStatus.PENDING,
    )
    assert len(pending) == 1
    candidate = pending[0].candidate
    rationale = candidate.save_rationale
    # 主内容（更长者）作为候选 content，其来源不被引用为分歧。
    assert candidate.content == "周报用 Markdown 列表并附变更说明"
    assert "分歧视角" in rationale
    # 少数视角（较短者）被保留在分歧摘要里。
    assert "项目周报用表格" in rationale
    # 主内容本身不出现在分歧引用中。
    assert "周报用 Markdown 列表并附变更说明（" not in rationale


def test_conflicting_business_progress_drops_cluster() -> None:
    """簇内成员 business_progress 出现 resolved/invalidated 对立时不产出团队候选。"""

    repository = _repo_with_members()
    repository.add(
        PrincipalContext(_MEMBERS[0]),
        _member_record(
            owner_id=_MEMBERS[0],
            subject="毛利率判断",
            content="毛利率企稳回升，风险已解除",
            embedding=_EMBEDDING_SEMANTIC_A,
            business_progress="resolved",
        ),
    )
    repository.add(
        PrincipalContext(_MEMBERS[1]),
        _member_record(
            owner_id=_MEMBERS[1],
            subject="毛利率判断",
            content="毛利率下行压力兑现，风险已兑现击穿",
            embedding=_EMBEDDING_SEMANTIC_A_DRIFT,
            business_progress="invalidated",
        ),
    )
    result = repository.extract_team_common_memories(
        team_owner_id=_TEAM_OWNER,
        member_owner_ids=_MEMBERS[:2],
        profile_id="project-work",
        effective_at=_NOW,
        similarity_threshold=0.85,
        min_cluster_size=2,
    )
    # 立场对立（resolved vs invalidated）的簇被丢弃，不产出候选。
    assert result.candidate_count == 0


def test_same_side_business_progress_clusters_normally() -> None:
    """簇内 business_progress 同侧（如都 resolved）时正常聚簇，弱校验不误拦。"""

    repository = _repo_with_members()
    for owner, progress in ((_MEMBERS[0], "resolved"), (_MEMBERS[1], "monitoring")):
        repository.add(
            PrincipalContext(owner),
            _member_record(
                owner_id=owner,
                subject="毛利率判断",
                content="毛利率企稳回升",
                embedding=_EMBEDDING_SEMANTIC_A,
                business_progress=progress,
            ),
        )
    result = repository.extract_team_common_memories(
        team_owner_id=_TEAM_OWNER,
        member_owner_ids=_MEMBERS[:2],
        profile_id="project-work",
        effective_at=_NOW,
        similarity_threshold=0.85,
        min_cluster_size=2,
    )
    assert result.candidate_count == 1


def test_idempotent_after_confirmed_no_duplicate_pending() -> None:
    """候选被 confirmed 后，同 subject+type 不再产出新 pending（幂等扩到 confirmed）。"""

    from dataclasses import replace as dc_replace

    repository = _repo_with_members()
    for owner in _MEMBERS[:2]:
        repository.add(
            PrincipalContext(owner),
            _member_record(
                owner_id=owner,
                subject="周报格式",
                content="项目周报用表格",
                embedding=_EMBEDDING_SEMANTIC_A,
            ),
        )
    args = dict(
        team_owner_id=_TEAM_OWNER,
        member_owner_ids=_MEMBERS[:2],
        profile_id="project-work",
        effective_at=_NOW,
        similarity_threshold=0.85,
        min_cluster_size=2,
    )
    # 第一次提取产出 1 个 pending。
    repository.extract_team_common_memories(**args)
    pending = repository.list_reviews(
        PrincipalContext(_TEAM_OWNER),
        status=ReviewStatus.PENDING,
    )
    assert len(pending) == 1
    review = pending[0]
    # 模拟该 pending 被确认（把状态置为 confirmed），复现"已沉淀为团队记忆"的状态。
    from uuid import uuid4

    repository._reviews[review.review_id] = dc_replace(
        review,
        status=ReviewStatus.CONFIRMED,
        decided_at=_NOW,
        resolved_memory_id=uuid4(),
    )
    # 第二次提取：同 subject+type 已 confirmed，不再产出新 pending。
    repository.extract_team_common_memories(**args)
    pending_after = repository.list_reviews(
        PrincipalContext(_TEAM_OWNER),
        status=ReviewStatus.PENDING,
    )
    assert len(pending_after) == 0


# ===== 全链接聚类 + 实体一致补聚（升级后行为）=====

# 用于传递漂移测试：A-B 相似 0.95（cos），B-C 相似 0.95，但 A-C 相似 0.0。
# 单链贪心会把 A/B/C 并一簇（链式效应）；全链接把 A/B 并一簇、C 单独。
_EMB_DRIFT_A = (1.0, 0.0, 0.0)
_EMB_DRIFT_B = (0.95, 0.31, 0.0)  # cos(A,B)≈0.95，cos(B,C)≈0.31*0.31/...≈0.95? 重算
_EMB_DRIFT_C = (0.0, 1.0, 0.0)


def test_complete_linkage_prevents_chaining_at_repository_level() -> None:
    """仓库层：A-B、B-C 相似达阈值但 A-C 正交，全链接不让 C 并入 A-B 簇。

    两个成员写了相似记忆（A-B cos 0.95 ≥ 0.70）+ 第三成员写了和 B 相似但和 A
    正交的记忆。若贪心（单链）会把三条并一簇（unique_owners=2 满足门槛）产出 1 候选；
    全链接要求簇内最大距离收敛，A-C 距离 1.0 > 0.30 阈值，C 不并入——产出 1 候选
    （仅 A、B），C 单独成簇但 unique_owners=1 不满足门槛被丢。
    """

    # 构造 A-B 相似 0.95、A-C 正交、B-C 也需 < 0.70 才能让 C 不并入。
    # 用 A=(1,0,0), B=(0.95, 0.31, 0)，C=(0,1,0)：
    #   cos(A,B)=0.95, cos(A,C)=0, cos(B,C)=0.31*1/(|B|*1)=0.31/0.999≈0.31 < 0.70
    repository = _repo_with_members()
    repository.add(
        PrincipalContext(_MEMBERS[0]),
        _member_record(
            owner_id=_MEMBERS[0],
            subject="周报格式",
            content="项目周报用表格",
            embedding=_EMB_DRIFT_A,
        ),
    )
    repository.add(
        PrincipalContext(_MEMBERS[1]),
        _member_record(
            owner_id=_MEMBERS[1],
            subject="周报格式",
            content="项目周报还是表格",
            embedding=_EMB_DRIFT_B,
        ),
    )
    repository.add(
        PrincipalContext(_MEMBERS[2]),
        _member_record(
            owner_id=_MEMBERS[2],
            subject="会议纪要",
            content="会议纪要用文本",
            embedding=_EMB_DRIFT_C,
        ),
    )
    result = repository.extract_team_common_memories(
        team_owner_id=_TEAM_OWNER,
        member_owner_ids=_MEMBERS,
        profile_id="project-work",
        effective_at=_NOW,
        similarity_threshold=0.70,
        min_cluster_size=2,
    )
    # A-B 一簇（2 不同成员）产出 1 候选；C 单独成簇但 unique_owners=1 不满足门槛。
    assert result.cluster_count == 1
    assert result.candidate_count == 1


def test_entity_overlap_merges_same_entity_different_wording() -> None:
    """同标的措辞差、向量在 0.50~0.70 中间地带 → 实体补聚并入同簇。

    两成员写了同标的（subject 都含"泡泡玛特"）但措辞不同的判断，向量相似度 0.55
    （低于聚类阈值 0.70 被全链接分两簇，但 ≥ 实体补聚的向量底线 0.50）。补聚应
    把两簇并一簇，产出 1 团队候选（否则会被漏聚，产出 0 候选）。
    """

    # cos = 0.55
    emb_a = (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    emb_b = (0.55, 0.835, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    repository = _repo_with_members()
    repository.add(
        PrincipalContext(_MEMBERS[0]),
        _member_record(
            owner_id=_MEMBERS[0],
            subject="泡泡玛特海外增长",
            content="泡泡玛特海外增长强劲",
            embedding=emb_a,
        ),
    )
    repository.add(
        PrincipalContext(_MEMBERS[1]),
        _member_record(
            owner_id=_MEMBERS[1],
            subject="泡泡玛特出海持续性",
            content="泡泡玛特出海持续性强",
            embedding=emb_b,
        ),
    )
    result = repository.extract_team_common_memories(
        team_owner_id=_TEAM_OWNER,
        member_owner_ids=_MEMBERS[:2],
        profile_id="project-work",
        effective_at=_NOW,
        similarity_threshold=0.70,
        min_cluster_size=2,
    )
    # 实体补聚应让两簇合一，产出 1 候选（若无补聚则 cluster_count=0、candidate=0）。
    assert result.cluster_count == 1
    assert result.candidate_count == 1


def test_entity_overlap_does_not_merge_different_dimensions() -> None:
    """同标的不同维度、向量 <0.50 底线 → 不并，各自单独成簇不产出候选。

    "泡泡玛特Q3超预期"与"泡泡玛特毛利率"都含实体，但向量正交（相似度 0.0 < 0.50
    底线）——属同标的不同维度判断，实体补聚不触发，各自 unique_owners=1 不满足门槛。
    """

    repository = _repo_with_members()
    repository.add(
        PrincipalContext(_MEMBERS[0]),
        _member_record(
            owner_id=_MEMBERS[0],
            subject="泡泡玛特Q3超预期",
            content="泡泡玛特Q3营收超预期",
            embedding=_EMBEDDING_SEMANTIC_A,
        ),
    )
    repository.add(
        PrincipalContext(_MEMBERS[1]),
        _member_record(
            owner_id=_MEMBERS[1],
            subject="泡泡玛特毛利率",
            content="泡泡玛特毛利率企稳",
            embedding=_EMBEDDING_SEMANTIC_B,
        ),
    )
    result = repository.extract_team_common_memories(
        team_owner_id=_TEAM_OWNER,
        member_owner_ids=_MEMBERS[:2],
        profile_id="project-work",
        effective_at=_NOW,
        similarity_threshold=0.70,
        min_cluster_size=2,
    )
    # 不并：各自单独成簇，unique_owners=1 不满足门槛 → 0 候选。
    assert result.candidate_count == 0
