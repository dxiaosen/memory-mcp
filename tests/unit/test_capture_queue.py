"""capture 队列（入队 + worker 异步抽取）单元测试。"""

from datetime import UTC, datetime

import pytest
from memory_mcp.core import (
    CaptureReprocessResult,
    CaptureStatus,
    IdempotencyConflictError,
    PrincipalContext,
    TurnEnvelope,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.composition import create_memory_service

from tests.support.fakes import (
    FakeCandidateExtractor,
    TestMemoryProfile,
    candidate_proposal,
)

_OBSERVED_AT = datetime(2026, 7, 29, 10, tzinfo=UTC)
_PRINCIPAL = PrincipalContext(
    owner_id="tenant-a:analyst-a",
)


def _turn(
    *,
    content: str = "以后项目周报默认用表格。",
    event_id: str | None = "evt-1",
    conversation_id: str = "conv-1",
    source_turn_id: str = "turn-1",
    payload_fingerprint: str = "fp-1",
) -> TurnEnvelope:
    return TurnEnvelope(
        profile_id="project-work",
        conversation_id=conversation_id,
        source_turn_id=source_turn_id,
        content=content,
        observed_at=_OBSERVED_AT,
        event_id=event_id,
        contract_version="1",
        payload_fingerprint=payload_fingerprint,
    )


def _service(
    *,
    extractor: FakeCandidateExtractor | None = None,
) -> object:
    return create_memory_service(
        InMemoryMemoryRepository(),
        [TestMemoryProfile()],
        candidate_extractor=extractor
        or FakeCandidateExtractor(
            (
                candidate_proposal(
                    "以后项目周报默认用表格",
                    proposed_owner_id=_PRINCIPAL.owner_id,
                    proposed_conversation_id="conv-1",
                    proposed_source_turn_id="turn-1",
                    proposed_observed_at=_OBSERVED_AT,
                ),
            ),
        ),
    )


def test_enqueue_capture_writes_pending_row_and_skips_extraction() -> None:
    """enqueue_capture 毫秒级返回 PENDING，不调用抽取器，content 脱敏后入库。"""

    extractor = FakeCandidateExtractor(())
    service = _service(extractor=extractor)
    result = service.enqueue_capture(_PRINCIPAL, _turn())
    assert result.status is CaptureStatus.PENDING
    assert result.outcomes == ()
    assert result.failure_code is None
    # 未触发模型抽取
    assert extractor.requests == []


def test_enqueue_capture_then_reprocess_completes_pending_row() -> None:
    """入队后 worker 抽取把 PENDING 行覆盖为 COMPLETED。"""

    service = _service()
    enqueued = service.enqueue_capture(_PRINCIPAL, _turn())
    assert enqueued.status is CaptureStatus.PENDING

    result = service._capture_service.run_capture_reprocess(batch_limit=10)
    assert isinstance(result, CaptureReprocessResult)
    assert result.processed_count == 1
    assert result.completed_count == 1
    assert result.reprocess_required_count == 0
    assert result.failed_count == 0
    assert result.has_more is False

    capture = service._capture_service._repository.get_capture(
        _PRINCIPAL,
        profile_id="project-work",
        conversation_id="conv-1",
        source_turn_id="turn-1",
        event_id="evt-1",
    )
    assert capture is not None
    assert capture.status is CaptureStatus.COMPLETED


def test_enqueue_capture_replays_existing_pending_row() -> None:
    """同幂等键重复入队直接 replay 返回，不产生新行。"""

    service = _service()
    first = service.enqueue_capture(_PRINCIPAL, _turn())
    assert first.status is CaptureStatus.PENDING
    second = service.enqueue_capture(_PRINCIPAL, _turn())
    assert second.status is CaptureStatus.PENDING
    assert second.replayed is True
    assert second.capture_id == first.capture_id


def test_enqueue_capture_rejects_reused_event_id_with_different_payload() -> None:
    """同 event_id 但不同 payload_fingerprint 触发幂等冲突。"""

    service = _service()
    service.enqueue_capture(_PRINCIPAL, _turn())
    with pytest.raises(IdempotencyConflictError):
        service.enqueue_capture(
            _PRINCIPAL,
            _turn(
                content="不同的内容应该冲突。",
                payload_fingerprint="fp-different",
            ),
        )


def test_run_capture_reprocess_empty_when_no_pending() -> None:
    """无 PENDING 行时返回空结果且 has_more=False。"""

    service = _service()
    result = service.run_capture_reprocess()
    assert result.processed_count == 0
    assert result.has_more is False


def test_run_capture_reprocess_marks_failed_extraction_as_reprocess_required() -> None:
    """抽取器抛非结构错误时 worker 把 PENDING 行标记为 REPROCESS_REQUIRED。"""

    extractor = FakeCandidateExtractor(
        (),
        failures_before_success=99,
        failure_exc=RuntimeError,
    )
    service = _service(extractor=extractor)
    service.enqueue_capture(_PRINCIPAL, _turn())
    result = service._capture_service.run_capture_reprocess()
    assert result.completed_count == 0
    assert result.reprocess_required_count == 1
    capture = service._capture_service._repository.get_capture(
        _PRINCIPAL,
        profile_id="project-work",
        conversation_id="conv-1",
        source_turn_id="turn-1",
        event_id="evt-1",
    )
    assert capture is not None
    assert capture.status is CaptureStatus.REPROCESS_REQUIRED
    assert capture.failure_code is not None


def test_run_capture_reprocess_marks_invalid_output_as_failed() -> None:
    """抽取器抛结构错误（InvalidModelOutputError）时 worker 标记 FAILED。"""

    extractor = FakeCandidateExtractor(
        (),
        failures_before_success=99,
    )
    service = _service(extractor=extractor)
    service.enqueue_capture(_PRINCIPAL, _turn())
    result = service._capture_service.run_capture_reprocess()
    assert result.completed_count == 0
    assert result.failed_count == 1
    capture = service._capture_service._repository.get_capture(
        _PRINCIPAL,
        profile_id="project-work",
        conversation_id="conv-1",
        source_turn_id="turn-1",
        event_id="evt-1",
    )
    assert capture is not None
    assert capture.status is CaptureStatus.FAILED
    assert capture.failure_code == "invalid_candidate_output"


def test_run_capture_reprocess_has_more_when_batch_full() -> None:
    """积压超过 batch_limit 时 has_more=True 供后台循环续批。"""

    service = _service()
    for index in range(5):
        service.enqueue_capture(
            _PRINCIPAL,
            _turn(
                event_id=f"evt-{index}",
                source_turn_id=f"turn-{index}",
                payload_fingerprint=f"fp-{index}",
                content=f"偏好 {index}。",
            ),
        )
    result = service._capture_service.run_capture_reprocess(batch_limit=2)
    assert result.processed_count == 2
    assert result.has_more is True
    # 第二批应处理剩余 3 条中的 2 条
    result2 = service._capture_service.run_capture_reprocess(batch_limit=2)
    assert result2.processed_count == 2
    assert result2.has_more is True


def test_capture_reprocess_result_validates_counts() -> None:
    """CaptureReprocessResult 校验非负整数与布尔 has_more。"""

    CaptureReprocessResult(
        processed_count=0,
        completed_count=0,
        reprocess_required_count=0,
        failed_count=0,
        has_more=False,
    )
    with pytest.raises(ValueError):
        CaptureReprocessResult(
            processed_count=-1,
            completed_count=0,
            reprocess_required_count=0,
            failed_count=0,
            has_more=False,
        )
    with pytest.raises(ValueError):
        CaptureReprocessResult(
            processed_count=0,
            completed_count=0,
            reprocess_required_count=0,
            failed_count=0,
            has_more="no",  # type: ignore[arg-type]
        )
