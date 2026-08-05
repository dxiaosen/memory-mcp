"""端到端主链路串联测试。

与 integration 层的区别：这里每个测试串联多步业务链路（捕获→召回→撤销→维护
等），而非验证单点规则。单点行为由 integration/contract 层覆盖。

每条链路跑真实 Core + InMemory Repository + FakeExtractor。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from memory_mcp.core import (
    AdmissionDecision,
    LifecycleStatus,
    MemoryNotFoundError,
    MessageRole,
    PrincipalContext,
    RecallQuery,
    TurnEnvelope,
    TurnMessage,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.composition import create_memory_service
from memory_mcp.core.ports import MemoryRelationPolicy

from tests.support.fakes import (
    FakeCandidateExtractor,
    SequentialCandidateExtractor,
    TestMemoryProfile,
    candidate_proposal,
)

_NOW = datetime(2026, 7, 29, 10, tzinfo=UTC)
_PRINCIPAL = PrincipalContext("analyst-a")


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


def _service(extractor=None, embedding_provider=None, profile=None):
    return create_memory_service(
        InMemoryMemoryRepository(),
        [profile or _profile()],
        candidate_extractor=extractor,
        embedding_provider=embedding_provider,
    )


def _turn(
    expression: str,
    *,
    turn_id: str | None = None,
    subject_hint: str = "weekly-report",
) -> TurnEnvelope:
    resolved = turn_id or f"turn-{expression[:8]}"
    return TurnEnvelope(
        profile_id="project-work",
        conversation_id="e2e-session",
        source_turn_id=resolved,
        content=expression,
        observed_at=_NOW,
        subject_hint=subject_hint,
        messages=(
            TurnMessage(
                role=MessageRole.USER,
                content=expression,
                message_id=f"msg-{resolved}",
            ),
        ),
    )


def test_e2e_capture_recall_revoke_maintenance_full_lifecycle() -> None:
    """主链路串联：捕获→召回命中→撤销→召回排除→历史保留。"""

    expression = "以后项目周报默认使用表格"
    extractor = FakeCandidateExtractor(
        (candidate_proposal(expression, content=expression),)
    )
    service = _service(extractor=extractor)

    # 1. 捕获 → auto_save
    result = service.capture_turn(_PRINCIPAL, _turn(expression))
    assert result.status.value == "completed"
    assert any(
        o.decision is AdmissionDecision.AUTO_SAVE for o in result.outcomes
    )

    # 2. 召回命中
    recalled = service.recall_memory(
        _PRINCIPAL,
        RecallQuery(
            profile_id="project-work", query="周报 表格", max_items=5
        ),
    )
    assert len(recalled.items) == 1
    assert "表格" in recalled.items[0].content

    memory = service.list_memories(_PRINCIPAL)[0]

    # 3. 撤销 → 召回排除
    service.revoke_memory(_PRINCIPAL, memory.item.memory_id)
    recalled_after_revoke = service.recall_memory(
        _PRINCIPAL,
        RecallQuery(
            profile_id="project-work", query="周报 表格", max_items=5
        ),
    )
    assert len(recalled_after_revoke.items) == 0

    # 4. 历史保留
    history = service.get_memory_history(_PRINCIPAL, memory.item.memory_id)
    assert len(history) == 1
    assert history[0].revision.lifecycle_status is LifecycleStatus.REVOKED


def test_e2e_capture_replacement_recall_updated_content() -> None:
    """替换链路：捕获 A→替换为 B→召回返回 B 最新内容，旧版 superseded。"""

    first = "项目周报默认使用表格"
    second = "以后项目周报改为 Markdown"
    extractor = SequentialCandidateExtractor(
        (
            candidate_proposal(first, content=first),
            candidate_proposal(second, content=second),
        )
    )
    service = _service(extractor=extractor)

    service.capture_turn(_PRINCIPAL, _turn(first, turn_id="r1"))
    service.capture_turn(_PRINCIPAL, _turn(second, turn_id="r2"))

    memories = service.list_memories(_PRINCIPAL)
    assert len(memories) == 1
    assert memories[0].current_revision.revision_number == 2
    assert "Markdown" in memories[0].current_revision.content

    # 召回返回最新内容
    recalled = service.recall_memory(
        _PRINCIPAL,
        RecallQuery(
            profile_id="project-work", query="周报 Markdown", max_items=5
        ),
    )
    assert len(recalled.items) == 1
    assert "Markdown" in recalled.items[0].content

    # 旧版 superseded
    history = service.get_memory_history(
        _PRINCIPAL, memories[0].item.memory_id
    )
    assert len(history) == 2
    assert history[1].revision.lifecycle_status is LifecycleStatus.SUPERSEDED


def test_e2e_owner_isolation_across_capture_recall_revoke() -> None:
    """owner 隔离链路：A 捕获→B 召回为空→B 越权读取被拒→B 越权撤销被拒。"""

    expression = "用户 A 的私有偏好"
    extractor = FakeCandidateExtractor(
        (candidate_proposal(expression, content=expression),)
    )
    service = _service(extractor=extractor)
    service.capture_turn(_PRINCIPAL, _turn(expression))

    other = PrincipalContext("user-b")

    # B 召回为空
    recalled = service.recall_memory(
        other,
        RecallQuery(
            profile_id="project-work", query="私有偏好", max_items=5
        ),
    )
    assert len(recalled.items) == 0

    # B 越权读取 memory_id 被拒
    owner_memory = service.list_memories(_PRINCIPAL)[0]
    with pytest.raises(MemoryNotFoundError):
        service.get_memory(other, owner_memory.item.memory_id)
