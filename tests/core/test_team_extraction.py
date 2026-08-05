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
            business_progress=None,
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
    """第二次提取相同相似记忆不重复创建 pending。"""

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
    assert first.candidate_count == 1
    assert second.candidate_count == 0
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
    """非成员的个人记忆即使相似也不会进入团队提取。"""

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
        member_owner_ids=(_MEMBERS[0],),
        profile_id="project-work",
        effective_at=_NOW,
        similarity_threshold=0.85,
        min_cluster_size=1,
    )
    assert result.member_count == 1
    assert result.memory_count == 1
    pending = repository.list_reviews(
        PrincipalContext(_TEAM_OWNER),
        status=ReviewStatus.PENDING,
    )
    assert len(pending) == 1


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


def _profile_registry_with_test_profile():
    from memory_mcp.core.ports import ProfileRegistry

    from tests.support.fakes import TestMemoryProfile

    registry = ProfileRegistry()
    registry.register(TestMemoryProfile())
    return registry
