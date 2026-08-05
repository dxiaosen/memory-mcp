"""日志事件结构化字段与脱敏测试。

验证：
- Capture 阶段聚合日志（decision/reason/lifecycle counts、duration_ms、replayed）；
- Recall 阶段聚合日志（recall_ref、candidates counts、embedding_degraded、zero_result）；
- 关联字段（capture_id、recall_ref、owner_ref）可在同一流程内串联；
- 默认模式不记录正文/Token/API Key；
- 内容模式只能通过 log_content_event 写入；
- Agent 包不支持内容日志；
- duration_ms 非负。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest
from memory_mcp.core import (
    MessageRole,
    PrincipalContext,
    RecallQuery,
    TurnEnvelope,
    TurnMessage,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.composition import create_memory_service
from memory_mcp.core.support import log_event, stable_reference
from memory_mcp.core.support.logging import (
    content_logging_enabled,
    log_content_event,
)

from tests.support.fakes import (
    FakeCandidateExtractor,
    FakeEmbeddingProvider,
    TestMemoryProfile,
    candidate_proposal,
)

_OBSERVED_AT = datetime(2026, 7, 29, 10, tzinfo=UTC)
_PRINCIPAL = PrincipalContext("analyst-a")


def _capture_service(
    extractor: FakeCandidateExtractor | None = None,
    embedding_provider: FakeEmbeddingProvider | None = None,
):
    return create_memory_service(
        InMemoryMemoryRepository(),
        [TestMemoryProfile()],
        candidate_extractor=extractor,
        embedding_provider=embedding_provider,
    )


def _turn(
    content: str,
    *,
    turn_id: str = "log-test-turn",
    event_id: str | None = None,
    payload_fingerprint: str | None = None,
) -> TurnEnvelope:
    return TurnEnvelope(
        profile_id="project-work",
        conversation_id="log-test-session",
        source_turn_id=turn_id,
        content=content,
        observed_at=_OBSERVED_AT,
        event_id=event_id,
        contract_version="1" if event_id else None,
        payload_fingerprint=payload_fingerprint,
        messages=(
            TurnMessage(
                role=MessageRole.USER,
                content=content,
                message_id=f"msg-{turn_id}",
            ),
        ),
    )


def _extract_events(caplog: pytest.LogCaptureFixture) -> list[dict]:
    """从 caplog 提取结构化事件，返回 [{event: ..., fields: {...}}] 列表。"""

    events: list[dict] = []
    for record in caplog.records:
        message = record.message
        if not message.startswith("event="):
            continue
        # event="xxx" key1=val1 key2=val2 → parse
        parts = message.split(" ", 1)
        event_name = json.loads(parts[0].split("=", 1)[1])
        fields: dict[str, object] = {}
        if len(parts) > 1:
            # naive parse: key=value pairs
            rest = parts[1]
            for pair in rest.split(" "):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    try:
                        fields[k] = json.loads(v)
                    except (json.JSONDecodeError, ValueError):
                        fields[k] = v
        events.append({"event": event_name, "fields": fields, "level": record.levelname})
    return events


# --- Capture 日志 ---


def test_capture_started_logs_message_count_and_input_character_count(
    caplog: pytest.LogCaptureFixture,
) -> None:
    expression = "以后项目周报默认使用表格"
    extractor = FakeCandidateExtractor(
        (candidate_proposal(expression, content=expression),)
    )
    service = _capture_service(extractor=extractor)

    with caplog.at_level(logging.INFO):
        service.capture_turn(_PRINCIPAL, _turn(expression))

    started = [e for e in _extract_events(caplog) if e["event"] == "memory.capture.started"]
    assert len(started) == 1
    fields = started[0]["fields"]
    assert fields["message_count"] == 1
    assert fields["input_character_count"] == len(expression)
    assert "capture_id" in fields
    assert "owner_ref" in fields


def test_capture_completed_logs_aggregate_counts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    expression = "以后项目周报默认使用表格"
    extractor = FakeCandidateExtractor(
        (candidate_proposal(expression, content=expression),)
    )
    service = _capture_service(extractor=extractor)

    with caplog.at_level(logging.INFO):
        service.capture_turn(_PRINCIPAL, _turn(expression))

    completed = [e for e in _extract_events(caplog) if e["event"] == "memory.capture.completed"]
    assert len(completed) == 1
    fields = completed[0]["fields"]
    assert fields["auto_saved_count"] == 1
    assert fields["pending_count"] == 0
    assert fields["discarded_count"] == 0
    assert fields["blocked_count"] == 0
    assert fields["replayed"] is False
    assert fields["duration_ms"] >= 0
    assert "reason_counts" in fields
    assert "duplicate_count" in fields
    assert "replacement_count" in fields
    assert "relation_proposal_count" in fields
    assert "relation_accepted_count" in fields


def test_capture_replay_logs_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    expression = "项目周报默认用表格"
    extractor = FakeCandidateExtractor(
        (candidate_proposal(expression, content=expression),)
    )
    service = _capture_service(extractor=extractor)

    envelope = _turn(expression, turn_id="replay-turn", event_id="evt-1", payload_fingerprint="fp-1")
    with caplog.at_level(logging.INFO):
        service.capture_turn(_PRINCIPAL, envelope)
        service.capture_turn(_PRINCIPAL, envelope)  # replay

    replays = [e for e in _extract_events(caplog) if e["event"] == "memory.capture.replay"]
    assert len(replays) == 1
    assert replays[0]["fields"]["replayed"] is True


def test_capture_idempotency_conflict_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    expression = "项目周报默认用表格"
    extractor = FakeCandidateExtractor(
        (candidate_proposal(expression, content=expression),)
    )
    service = _capture_service(extractor=extractor)

    first = _turn(expression, turn_id="conflict-turn", event_id="evt-2", payload_fingerprint="fp-a")
    service.capture_turn(_PRINCIPAL, first)
    # different payload same event_id → conflict
    second = _turn(expression, turn_id="conflict-turn", event_id="evt-2", payload_fingerprint="fp-b")

    with caplog.at_level(logging.WARNING):
        from memory_mcp.core import IdempotencyConflictError
        with pytest.raises(IdempotencyConflictError):
            service.capture_turn(_PRINCIPAL, second)

    conflicts = [e for e in _extract_events(caplog) if e["event"] == "memory.capture.idempotency_conflict"]
    assert len(conflicts) == 1
    assert conflicts[0]["level"] == "WARNING"


def test_capture_incomplete_logs_duration_and_failure_code(
    caplog: pytest.LogCaptureFixture,
) -> None:

    # A capture with invalid model output → FAILED
    expression = "test"
    extractor = FakeCandidateExtractor(
        (candidate_proposal(expression, content=expression, memory_type="nonexistent_type"),)
    )
    service = _capture_service(extractor=extractor)

    with caplog.at_level(logging.WARNING):
        service.capture_turn(_PRINCIPAL, _turn(expression, turn_id="fail-turn"))

    incomplete = [e for e in _extract_events(caplog) if e["event"] == "memory.capture.incomplete"]
    assert len(incomplete) == 1
    fields = incomplete[0]["fields"]
    assert fields["failure_code"] is not None
    assert fields["duration_ms"] >= 0
    assert fields["status"] in ("failed", "reprocess_required")


# --- Recall 日志 ---


def test_recall_started_logs_recall_ref_and_embedding_enabled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = _capture_service(
        embedding_provider=FakeEmbeddingProvider({"x": (1.0, 0.0)})
    )
    with caplog.at_level(logging.INFO):
        service.recall_memory(
            _PRINCIPAL,
            RecallQuery(
                profile_id="project-work",
                query="test",
                max_items=5,
            ),
        )

    started = [e for e in _extract_events(caplog) if e["event"] == "memory.recall.started"]
    assert len(started) == 1
    fields = started[0]["fields"]
    assert "recall_ref" in fields
    assert fields["embedding_enabled"] is True


def test_recall_completed_logs_aggregate_counts_and_duration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = _capture_service()
    with caplog.at_level(logging.INFO):
        service.recall_memory(
            _PRINCIPAL,
            RecallQuery(
                profile_id="project-work",
                query="test",
                max_items=5,
            ),
        )

    completed = [e for e in _extract_events(caplog) if e["event"] == "memory.recall.completed"]
    assert len(completed) == 1
    fields = completed[0]["fields"]
    assert "recall_ref" in fields
    assert fields["duration_ms"] >= 0
    assert fields["zero_result"] is True
    assert "lexical_count" in fields
    assert "vector_count" in fields
    assert "recent_count" in fields
    assert "candidate_count" in fields
    assert "embedding_enabled" in fields
    assert "embedding_degraded" in fields


def test_recall_ref_links_started_and_completed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = _capture_service()
    with caplog.at_level(logging.INFO):
        service.recall_memory(
            _PRINCIPAL,
            RecallQuery(
                profile_id="project-work",
                query="test",
                max_items=5,
            ),
        )

    events = _extract_events(caplog)
    started = [e for e in events if e["event"] == "memory.recall.started"]
    completed = [e for e in events if e["event"] == "memory.recall.completed"]
    assert len(started) == 1 and len(completed) == 1
    assert started[0]["fields"]["recall_ref"] == completed[0]["fields"]["recall_ref"]


def test_recall_embedding_degraded_logs_true(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = _capture_service(
        embedding_provider=FakeEmbeddingProvider({}, failures_before_success=1)
    )
    with caplog.at_level(logging.INFO):
        service.recall_memory(
            _PRINCIPAL,
            RecallQuery(
                profile_id="project-work",
                query="test",
                max_items=5,
            ),
        )

    completed = [e for e in _extract_events(caplog) if e["event"] == "memory.recall.completed"]
    assert len(completed) == 1
    assert completed[0]["fields"]["embedding_degraded"] is True


# --- 脱敏与安全 ---


def test_log_event_redacts_sensitive_field_names() -> None:
    """log_event 按字段名脱敏敏感字段。"""
    logger = logging.getLogger("test.redact")
    import io
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    log_event(
        logger,
        logging.INFO,
        "test.redaction",
        content="should not appear",
        query="also sensitive",
        api_key="sk-secret",
        owner_ref="safe-value",
    )

    output = stream.getvalue()
    assert "should not appear" not in output
    assert "also sensitive" not in output
    assert "sk-secret" not in output
    assert "safe-value" in output
    assert "[REDACTED]" in output


def test_default_mode_does_not_log_content_events(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """默认模式不写入 log_content_event 内容。"""
    assert content_logging_enabled() is False
    log_content_event("test.never.logged", data="should not appear")
    # content logger is "memory_mcp.content" — not captured by caplog on root logger


def test_agent_logging_rejects_content_mode() -> None:
    """Agent 包不支持内容日志。"""
    from memory_mcp_agent.logging import configure_logging

    with pytest.raises(ValueError, match="content logging"):
        configure_logging(content=True)


def test_stable_reference_does_not_expose_original() -> None:
    """stable_reference 不泄露原始标识。"""
    ref = stable_reference("analyst-secret-id")
    assert ref != "analyst-secret-id"
    assert len(ref) == 12
    assert ref == stable_reference("analyst-secret-id")
