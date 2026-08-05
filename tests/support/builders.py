"""共享测试 builder：构造 TurnEnvelope、service、候选记录等高频领域对象。

原则：
- 简单值直接在测试中写清楚，只提取真正高频且跨文件复用的构造；
- 不复制准入/召回/生命周期算法，只构造输入；
- builder 无副作用，不隐藏关键业务差异。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from memory_mcp.core import (
    AssertionKind,
    CreateMemoryCommand,
    EvidenceSourceType,
    ExpressionBasis,
    LifecycleStatus,
    MemoryService,
    MessageRole,
    PrincipalContext,
    TurnEnvelope,
    TurnMessage,
    VerificationStatus,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.composition import create_memory_service
from memory_mcp.core.domain import (
    Evidence,
    MemoryItem,
    MemoryRecord,
    MemoryRevision,
    SensitivityLevel,
)
from memory_mcp.core.ports import MemoryProfile

from tests.support.fakes import (
    FakeCandidateExtractor,
    FakeEmbeddingProvider,
    TestMemoryProfile,
    candidate_proposal,
    project_preference_command,
)

NOW = datetime(2026, 7, 29, 10, tzinfo=UTC)
ANALYST = PrincipalContext("analyst-a")
OWNER_A = PrincipalContext("owner-a")
OWNER_B = PrincipalContext("owner-b")


def turn(
    content: str,
    *,
    profile_id: str = "project-work",
    turn_id: str = "turn-1",
    conversation_id: str = "conversation-1",
    observed_at: datetime = NOW,
    subject_hint: str | None = "weekly-report",
    role: MessageRole = MessageRole.USER,
) -> TurnEnvelope:
    """构造单消息 TurnEnvelope，默认 user 角色与 weekly-report 主题提示。"""

    return TurnEnvelope(
        profile_id=profile_id,
        conversation_id=conversation_id,
        source_turn_id=turn_id,
        content=content,
        observed_at=observed_at,
        subject_hint=subject_hint,
        messages=(
            TurnMessage(
                role=role,
                content=content,
                message_id=f"message-{turn_id}",
            ),
        ),
    )


def service(
    repository: InMemoryMemoryRepository | None = None,
    profiles: Sequence[MemoryProfile] = (TestMemoryProfile(),),
    *,
    extractor: FakeCandidateExtractor | None = None,
    embedding_provider: FakeEmbeddingProvider | None = None,
) -> MemoryService:
    """构造注入了 Fake extractor/embedding 的 MemoryService。"""

    return create_memory_service(
        repository or InMemoryMemoryRepository(),
        list(profiles),
        candidate_extractor=extractor,
        embedding_provider=embedding_provider,
    )


def capture(
    svc: MemoryService,
    extractor: FakeCandidateExtractor,
    *,
    text: str,
    content: str,
    turn_id: str,
    subject: str = "weekly-report",
    expression_basis: ExpressionBasis = ExpressionBasis.EXPLICIT,
    assertion_kind: AssertionKind = AssertionKind.USER_VIEW,
    principal: PrincipalContext = OWNER_A,
    profile_id: str = "general-work",
) -> object:
    """单步捕获：设置 extractor 返回单条候选后执行 capture_turn。"""

    extractor.proposals = (
        candidate_proposal(
            text,
            subject=subject,
            content=content,
            expression_basis=expression_basis,
            assertion_kind=assertion_kind,
        ),
    )
    return svc.capture_turn(
        principal,
        turn(text, profile_id=profile_id, turn_id=turn_id, subject_hint=subject),
    )


def record(
    *,
    subject: str = "weekly-report",
    memory_type: str = "preference",
    content: str = "项目周报默认使用表格",
    owner_id: str = "owner-a",
    profile_id: str = "project-work",
    embedding: tuple[float, ...] | None = None,
    observed_at: datetime = NOW,
    lifecycle_status: LifecycleStatus = LifecycleStatus.ACTIVE,
    valid_until: datetime | None = None,
    memory_id: UUID | None = None,
) -> MemoryRecord:
    """构造一条带 embedding 的活动记忆，绕过捕获流程直接写库。"""

    resolved_memory_id = memory_id or uuid4()
    revision_id = uuid4()
    return MemoryRecord(
        item=MemoryItem(
            memory_id=resolved_memory_id,
            owner_id=owner_id,
            profile_id=profile_id,
            subject=subject,
            memory_type=memory_type,
            created_at=observed_at,
        ),
        current_revision=MemoryRevision(
            revision_id=revision_id,
            memory_id=resolved_memory_id,
            owner_id=owner_id,
            revision_number=1,
            content=content,
            assertion_kind=AssertionKind.USER_VIEW,
            lifecycle_status=lifecycle_status,
            business_progress=None,
            save_rationale="测试记忆",
            observed_at=observed_at,
            created_at=observed_at,
            extraction_confidence=0.9,
            verification_status=VerificationStatus.USER_ASSERTED,
            sensitivity_level=SensitivityLevel.CONFIDENTIAL,
            valid_from=observed_at,
            valid_until=valid_until,
            embedding=embedding,
        ),
        evidence=(
            Evidence(
                evidence_id=uuid4(),
                memory_id=resolved_memory_id,
                revision_id=revision_id,
                owner_id=owner_id,
                source_turn_id="turn-1",
                source_expression=content,
                observed_at=observed_at,
                created_at=observed_at,
                source_role=MessageRole.USER,
                source_type=EvidenceSourceType.CONVERSATION,
            ),
        ),
    )


def preference_command(
    *,
    lifecycle_status: LifecycleStatus = LifecycleStatus.ACTIVE,
    business_progress: str | None = None,
) -> CreateMemoryCommand:
    """project-work preference 命令别名（保持与 fakes 一致）。"""

    return project_preference_command(
        lifecycle_status=lifecycle_status,
        business_progress=business_progress,
    )
