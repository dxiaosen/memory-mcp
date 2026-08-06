"""C1 时效衰减召回排序的契约测试。

覆盖：
- ``_apply_time_decay`` 纯函数：半衰期内部分衰减、半衰期外封顶在权重内、
  零分不衰减、负年龄（未来记忆）不衰减；
- 端到端排序：同样相关的新旧证据，新的因衰减更少而排前。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
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
from memory_mcp.core.application.recall_service import _apply_time_decay
from memory_mcp.core.domain import Evidence
from memory_mcp.core.ports import MemoryMetadataPolicy
from memory_mcp.profiles import InvestmentResearchProfile

_PRINCIPAL = PrincipalContext("owner-a")
_BASE_NOW = datetime(2026, 7, 30, 10, tzinfo=UTC)


def _evidence_claim_record(
    *,
    subject: str,
    content: str,
    observed_at: datetime,
    owner_id: str = "owner-a",
    valid_until: datetime | None = None,
) -> MemoryRecord:
    """构造一条投研 evidence_claim 活动记忆。"""

    memory_id = uuid4()
    revision_id = uuid4()
    return MemoryRecord(
        item=MemoryItem(
            memory_id=memory_id,
            owner_id=owner_id,
            profile_id="investment-research",
            subject=subject,
            memory_type="evidence_claim",
            created_at=observed_at,
        ),
        current_revision=MemoryRevision(
            revision_id=revision_id,
            memory_id=memory_id,
            owner_id=owner_id,
            revision_number=1,
            content=content,
            assertion_kind=AssertionKind.EXTERNAL_FACT,
            lifecycle_status=LifecycleStatus.ACTIVE,
            business_progress=None,
            save_rationale="测试证据",
            observed_at=observed_at,
            created_at=observed_at,
            extraction_confidence=0.95,
            verification_status=VerificationStatus.UNVERIFIED,
            sensitivity_level=SensitivityLevel.INTERNAL,
            valid_from=observed_at,
            valid_until=valid_until,
        ),
        evidence=(
            Evidence(
                evidence_id=uuid4(),
                memory_id=memory_id,
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


def test_apply_time_decay_decays_old_memory_within_weight() -> None:
    """半衰期（90 天）外的记忆最多衰减权重（15%）。"""

    policy = MemoryMetadataPolicy(validity_days=90)
    observed = _BASE_NOW - timedelta(days=180)
    decayed = _apply_time_decay(
        0.8,
        "evidence_claim",
        observed,
        {"evidence_claim": policy},
        _BASE_NOW,
    )
    # age=2*half_life => decay_factor=0.25 => decayed = 1 - 0.75*0.15 = 0.8875
    assert decayed == pytest.approx(0.8 * (1 - 0.75 * 0.15), abs=1e-5)


def test_apply_time_decay_keeps_recent_memory_unreduced() -> None:
    """当天记忆（age=0）不衰减。"""

    policy = MemoryMetadataPolicy(validity_days=90)
    decayed = _apply_time_decay(
        0.8,
        "evidence_claim",
        _BASE_NOW,
        {"evidence_claim": policy},
        _BASE_NOW,
    )
    assert decayed == pytest.approx(0.8, abs=1e-5)


def test_apply_time_decay_skips_zero_score() -> None:
    """零分不衰减（避免无中生有）。"""

    policy = MemoryMetadataPolicy(validity_days=90)
    decayed = _apply_time_decay(
        0.0,
        "evidence_claim",
        _BASE_NOW - timedelta(days=365),
        {"evidence_claim": policy},
        _BASE_NOW,
    )
    assert decayed == 0.0


def test_apply_time_decay_skips_future_observed() -> None:
    """observed_at 在 effective_at 之后（未来事件）不衰减。"""

    policy = MemoryMetadataPolicy(validity_days=90)
    decayed = _apply_time_decay(
        0.8,
        "evidence_claim",
        _BASE_NOW + timedelta(days=10),
        {"evidence_claim": policy},
        _BASE_NOW,
    )
    assert decayed == pytest.approx(0.8, abs=1e-5)


def test_apply_time_decay_falls_back_to_default_half_life() -> None:
    """类型未声明 validity_days 时回退默认半衰期 90 天。"""

    observed = _BASE_NOW - timedelta(days=90)
    decayed = _apply_time_decay(
        0.8,
        "evidence_claim",
        observed,
        {},  # 无策略 -> 默认 90 天
        _BASE_NOW,
    )
    # age=half_life => decay_factor=0.5 => decayed = 1 - 0.5*0.15 = 0.925
    assert decayed == pytest.approx(0.8 * 0.925, abs=1e-5)


def test_recall_ranks_newer_evidence_above_older_of_equal_relevance() -> None:
    """同相关度的新旧证据：新的因衰减更少而排前。"""

    now = [_BASE_NOW]

    def clock() -> datetime:
        return now[0]

    repository = InMemoryMemoryRepository()
    service = MemoryService(
        repository,
        ProfileRegistry(),
        sensitive_guard=RegexSensitiveContentGuard(),
        clock=clock,
    )
    service.register_profile(InvestmentResearchProfile())

    # 旧证据（120 天前）与新证据（当天）相关度相同；均保持有效（valid_until 在未来）
    # 以隔离时效衰减的作用，避免被读取谓词的 valid_until 过滤掉。
    far_future = _BASE_NOW + timedelta(days=365)
    old_observed = _BASE_NOW - timedelta(days=120)
    old = _evidence_claim_record(
        subject="example-company-revenue-2024",
        content="示例公司 2024 年企业收入同比增长 18%",
        observed_at=old_observed,
        valid_until=far_future,
    )
    new = _evidence_claim_record(
        subject="example-company-revenue-2025",
        content="示例公司 2025 年企业收入同比增长 18%",
        observed_at=_BASE_NOW,
        valid_until=far_future,
    )
    repository.add(_PRINCIPAL, old)
    repository.add(_PRINCIPAL, new)

    recalled = service.recall_memory(
        _PRINCIPAL,
        RecallQuery(
            profile_id="investment-research",
            query="示例公司企业收入同比增长 18%",
            max_items=2,
        ),
    )
    assert len(recalled.items) == 2
    # 新证据（2025）应排第一。
    assert recalled.items[0].subject == "example-company-revenue-2025"
    assert recalled.items[0].relevance_score >= recalled.items[1].relevance_score
