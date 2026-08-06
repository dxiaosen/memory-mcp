"""新增投研工具测试：search_memories / batch_confirm_pending / get_memory_stats。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from memory_mcp.core import (
    MessageRole,
    PrincipalContext,
    TurnEnvelope,
    TurnMessage,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.composition import create_memory_service

from tests.support.fakes import (
    FakeCandidateExtractor,
    TestMemoryProfile,
    candidate_proposal,
)

_NOW = datetime(2026, 7, 29, 10, tzinfo=UTC)
_PRINCIPAL = PrincipalContext("analyst-a")


def _service():
    return create_memory_service(
        InMemoryMemoryRepository(),
        [TestMemoryProfile()],
        candidate_extractor=FakeCandidateExtractor(
            (candidate_proposal("项目周报默认用表格", content="项目周报默认用表格"),)
        ),
    )


def _turn(content: str, *, turn_id: str = "search-turn") -> TurnEnvelope:
    return TurnEnvelope(
        profile_id="project-work",
        conversation_id="search-session",
        source_turn_id=turn_id,
        content=content,
        observed_at=_NOW,
        messages=(
            TurnMessage(role=MessageRole.USER, content=content, message_id=f"msg-{turn_id}"),
        ),
    )


# ── search_memories ──


def test_search_memories_returns_matching_records() -> None:
    service = _service()
    service.capture_turn(_PRINCIPAL, _turn("项目周报默认用表格", turn_id="t1"))
    results = service.search_memories(
        _PRINCIPAL,
        query="项目周报默认用表格",
        profile_id="project-work",
    )
    assert len(results) == 1
    assert "表格" in results[0].current_revision.content


def test_search_memories_no_match_returns_empty() -> None:
    service = _service()
    service.capture_turn(_PRINCIPAL, _turn("项目周报默认用表格", turn_id="t2"))
    results = service.search_memories(
        _PRINCIPAL,
        query="quantum physics experiment",
        profile_id="project-work",
    )
    assert len(results) == 0


def test_search_memories_rejects_empty_query() -> None:
    service = _service()
    with pytest.raises(ValueError, match="query"):
        service.search_memories(_PRINCIPAL, query="", profile_id="project-work")


def test_search_memories_rejects_invalid_limit() -> None:
    service = _service()
    with pytest.raises(ValueError, match="limit"):
        service.search_memories(_PRINCIPAL, query="test", profile_id="project-work", limit=0)
    with pytest.raises(ValueError, match="limit"):
        service.search_memories(_PRINCIPAL, query="test", profile_id="project-work", limit=101)


def test_search_memories_filters_by_memory_type() -> None:
    service = _service()
    service.capture_turn(_PRINCIPAL, _turn("项目周报默认用表格", turn_id="t3"))
    results = service.search_memories(
        _PRINCIPAL,
        query="项目周报默认用表格",
        profile_id="project-work",
        memory_type="preference",
    )
    assert len(results) == 1
    results_none = service.search_memories(
        _PRINCIPAL,
        query="项目周报默认用表格",
        profile_id="project-work",
        memory_type="ongoing_item",
    )
    assert len(results_none) == 0


# ── get_memory_stats ──


def test_get_memory_stats_returns_counts() -> None:
    service = _service()
    service.capture_turn(_PRINCIPAL, _turn("项目周报默认用表格", turn_id="t4"))
    stats = service.get_memory_stats(_PRINCIPAL)
    assert stats["total_active_memories"] == 1
    assert "by_memory_type" in stats
    assert "by_profile" in stats
    assert stats["pending_review_count"] == 0


def test_get_memory_stats_empty_owner() -> None:
    service = _service()
    stats = service.get_memory_stats(PrincipalContext("empty-user"))
    assert stats["total_active_memories"] == 0
    assert stats["pending_review_count"] == 0


# ── batch_confirm_reviews ──


def test_batch_confirm_reviews_confirms_all() -> None:
    """需要 pending review，所以用 SequentialExtractor 制造 ambiguous conflict。"""
    from tests.support.fakes import SequentialCandidateExtractor

    service = create_memory_service(
        InMemoryMemoryRepository(),
        [TestMemoryProfile()],
        candidate_extractor=SequentialCandidateExtractor(
            (
                candidate_proposal("项目周报用表格", content="项目周报用表格"),
                candidate_proposal("项目周报也可以用文本", content="项目周报也可以用文本"),
            )
        ),
    )
    service.capture_turn(_PRINCIPAL, _turn("项目周报用表格", turn_id="batch-1"))
    service.capture_turn(_PRINCIPAL, _turn("项目周报也可以用文本", turn_id="batch-2"))

    pending = service.list_pending_reviews(_PRINCIPAL)
    assert len(pending) >= 1

    confirmed, failed = service.batch_confirm_reviews(
        _PRINCIPAL,
        tuple(r.review_id for r in pending),
    )
    assert len(confirmed) == len(pending)
    assert len(failed) == 0


def test_batch_confirm_reviews_handles_missing() -> None:
    service = _service()
    from uuid import uuid4

    confirmed, failed = service.batch_confirm_reviews(
        _PRINCIPAL,
        (uuid4(), uuid4()),
    )
    assert len(confirmed) == 0
    assert len(failed) == 2
