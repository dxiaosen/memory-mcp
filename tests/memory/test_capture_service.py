import logging
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from agent_lab.memory import (
    AdmissionDecision,
    AssertionKind,
    CandidateDurability,
    CaptureStatus,
    ExpressionBasis,
    PrincipalContext,
    ReviewNotFoundError,
    ReviewStatus,
    TurnEnvelope,
)
from agent_lab.memory.adapters import InMemoryMemoryRepository
from agent_lab.memory.adapters.sqlite import (
    SQLiteMemoryRepository,
    connection_factory,
)
from agent_lab.memory.adapters.sqlite.runtime import apply_migrations
from agent_lab.memory.composition import create_memory_service
from memory.fakes import (
    AlternateScenarioPolicy,
    FakeCandidateExtractor,
    TestScenarioPolicy,
    candidate_proposal,
)

_OBSERVED_AT = datetime(2026, 7, 29, 10, tzinfo=UTC)


@pytest.fixture
def capture_database_path() -> Iterator[Path]:
    directory = Path(".agent-lab/test-memory")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"capture-{uuid4().hex}.db"
    try:
        assert apply_migrations(path) == (
            "0001_memory_core.sql",
            "0002_memory_capture.sql",
            "0003_mcp_events.sql",
        )
        yield path
    finally:
        path.unlink(missing_ok=True)


def test_capture_assigns_all_four_decisions_and_preserves_relative_time() -> None:
    turn_text = (
        "以后项目周报默认用表格。"
        "接口重构下周还要继续跟进。"
        "这次回答短一点。"
        "看起来我可能喜欢蓝色。"
    )
    extractor = FakeCandidateExtractor(
        (
            candidate_proposal(
                "以后项目周报默认用表格",
                proposed_owner_id="model-chosen-owner",
                proposed_conversation_id="model-conversation",
                proposed_source_turn_id="model-turn",
                proposed_observed_at=datetime(2025, 1, 1, tzinfo=UTC),
            ),
            candidate_proposal(
                "接口重构下周还要继续跟进",
                subject="interface-refactor",
                memory_type="ongoing_item",
                content="接口重构下周继续跟进",
                assertion_kind=AssertionKind.USER_PROVIDED_FACT,
                business_progress="open",
                original_time_expression="下周",
                normalized_time=datetime(2026, 8, 3, tzinfo=UTC),
            ),
            candidate_proposal(
                "这次回答短一点",
                content="当前回答使用简短格式",
                durability=CandidateDurability.TEMPORARY,
            ),
            candidate_proposal(
                "看起来我可能喜欢蓝色",
                content="用户可能偏好蓝色",
                assertion_kind=AssertionKind.SYSTEM_INFERENCE,
                expression_basis=ExpressionBasis.INFERRED,
            ),
        )
    )
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [TestScenarioPolicy()],
        candidate_extractor=extractor,
    )
    principal = PrincipalContext("analyst-a")

    result = service.capture_turn(principal, _turn(turn_text))

    assert result.status is CaptureStatus.COMPLETED
    assert [outcome.decision for outcome in result.outcomes] == [
        AdmissionDecision.AUTO_SAVE,
        AdmissionDecision.AUTO_SAVE,
        AdmissionDecision.DISCARD,
        AdmissionDecision.PENDING,
    ]
    assert result.metadata.model_id == "fake-structured-model"
    assert result.metadata.prompt_version == "capture-prompt-v1"
    assert result.metadata.schema_version == "candidate-v1"
    assert result.metadata.policy_version == "project-work-v1"
    assert extractor.requests[0].allowed_memory_types == {
        "preference",
        "ongoing_item",
        "stable_context",
    }
    assert (
        extractor.requests[0].capture_guidance
        == "Capture durable project-work context."
    )

    memories = service.list_memories(principal)
    assert len(memories) == 2
    assert {record.item.owner_id for record in memories} == {"analyst-a"}
    assert {record.evidence[0].conversation_id for record in memories} == {
        "conversation-1"
    }
    assert {record.evidence[0].source_turn_id for record in memories} == {"turn-1"}
    ongoing = next(
        record for record in memories if record.item.memory_type == "ongoing_item"
    )
    assert ongoing.current_revision.original_time_expression == "下周"
    assert ongoing.current_revision.normalized_time == datetime(
        2026,
        8,
        3,
        tzinfo=UTC,
    )

    pending = service.list_pending_reviews(principal)
    assert len(pending) == 1
    assert pending[0].candidate.owner_id == "analyst-a"
    assert pending[0].candidate.conversation_id == "conversation-1"
    assert pending[0].candidate.source_turn_id == "turn-1"
    assert pending[0].candidate.observed_at == _OBSERVED_AT


def test_sensitive_text_is_redacted_before_model_and_never_persisted(
    capture_database_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "secret-password-123"
    safe_expression = "项目周报默认用表格"
    extractor = FakeCandidateExtractor((candidate_proposal(safe_expression),))
    repository = SQLiteMemoryRepository(connection_factory(capture_database_path))
    service = create_memory_service(
        repository,
        [TestScenarioPolicy()],
        candidate_extractor=extractor,
    )

    with caplog.at_level(logging.DEBUG):
        result = service.capture_turn(
            PrincipalContext("analyst-a"),
            _turn(f"密码是 {secret}。{safe_expression}。"),
        )

    assert result.status is CaptureStatus.COMPLETED
    assert [outcome.decision for outcome in result.outcomes] == [
        AdmissionDecision.BLOCKED,
        AdmissionDecision.AUTO_SAVE,
    ]
    model_text = extractor.requests[0].content
    assert secret not in model_text
    assert "[REDACTED:credential]" in model_text
    assert secret.encode() not in capture_database_path.read_bytes()
    assert secret not in caplog.text
    assert all(not hasattr(outcome, "content") for outcome in result.outcomes)


def test_capture_is_idempotent_across_sqlite_repository_reopen(
    capture_database_path: Path,
) -> None:
    extractor = FakeCandidateExtractor(
        (
            candidate_proposal("以后项目周报默认用表格"),
            candidate_proposal(
                "我可能也喜欢要点",
                content="用户可能偏好要点",
                assertion_kind=AssertionKind.SYSTEM_INFERENCE,
                expression_basis=ExpressionBasis.INFERRED,
            ),
        )
    )
    principal = PrincipalContext("analyst-a")
    turn = _turn("以后项目周报默认用表格。我可能也喜欢要点。")
    service = create_memory_service(
        SQLiteMemoryRepository(connection_factory(capture_database_path)),
        [TestScenarioPolicy()],
        candidate_extractor=extractor,
    )

    first = service.capture_turn(principal, turn)
    second = service.capture_turn(principal, turn)

    assert first.capture_id == second.capture_id
    assert second.replayed is True
    assert second.outcomes == first.outcomes
    assert len(extractor.requests) == 1

    reopened_extractor = FakeCandidateExtractor()
    reopened = create_memory_service(
        SQLiteMemoryRepository(connection_factory(capture_database_path)),
        [TestScenarioPolicy()],
        candidate_extractor=reopened_extractor,
    )
    third = reopened.capture_turn(principal, turn)
    assert third.capture_id == first.capture_id
    assert third.replayed is True
    assert reopened_extractor.requests == []

    with closing(connection_factory(capture_database_path)()) as connection:
        assert (
            connection.execute("SELECT count(*) FROM memory_capture_runs").fetchone()[0]
            == 1
        )
        assert (
            connection.execute("SELECT count(*) FROM memory_items").fetchone()[0] == 1
        )
        assert (
            connection.execute("SELECT count(*) FROM memory_evidence").fetchone()[0]
            == 1
        )
        assert (
            connection.execute("SELECT count(*) FROM memory_review_items").fetchone()[0]
            == 1
        )


def test_sensitive_model_output_is_blocked_before_persistence(
    capture_database_path: Path,
) -> None:
    secret = "model-secret-456"
    extractor = FakeCandidateExtractor(
        (
            candidate_proposal(
                "这是一段安全输入",
                content=f"密码是 {secret}",
            ),
        )
    )
    service = create_memory_service(
        SQLiteMemoryRepository(connection_factory(capture_database_path)),
        [TestScenarioPolicy()],
        candidate_extractor=extractor,
    )
    principal = PrincipalContext("analyst-a")

    result = service.capture_turn(principal, _turn("这是一段安全输入。"))

    assert [outcome.decision for outcome in result.outcomes] == [
        AdmissionDecision.BLOCKED
    ]
    assert service.list_memories(principal) == ()
    assert service.list_pending_reviews(principal) == ()
    assert secret.encode() not in capture_database_path.read_bytes()


def test_retryable_failure_is_reprocessed_without_duplicates() -> None:
    extractor = FakeCandidateExtractor(
        (candidate_proposal("以后项目周报默认用表格"),),
        failures_before_success=1,
    )
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [TestScenarioPolicy()],
        candidate_extractor=extractor,
    )
    principal = PrincipalContext("analyst-a")
    turn = _turn("以后项目周报默认用表格。")

    failed = service.capture_turn(principal, turn)
    completed = service.capture_turn(principal, turn)
    replayed = service.capture_turn(principal, turn)

    assert failed.status is CaptureStatus.REPROCESS_REQUIRED
    assert failed.failure_code == "processing_interrupted"
    assert completed.status is CaptureStatus.COMPLETED
    assert completed.capture_id == failed.capture_id
    assert completed.was_reprocessed is True
    assert replayed.replayed is True
    assert len(extractor.requests) == 2
    assert len(service.list_memories(principal)) == 1


def test_invalid_model_type_fails_safely_and_is_not_reprocessed() -> None:
    extractor = FakeCandidateExtractor(
        (
            candidate_proposal(
                "以后项目周报默认用表格",
                memory_type="not-registered",
            ),
        )
    )
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [TestScenarioPolicy()],
        candidate_extractor=extractor,
    )
    principal = PrincipalContext("analyst-a")
    turn = _turn("以后项目周报默认用表格。")

    first = service.capture_turn(principal, turn)
    second = service.capture_turn(principal, turn)

    assert first.status is CaptureStatus.FAILED
    assert first.failure_code == "invalid_candidate_output"
    assert first.outcomes == ()
    assert second.replayed is True
    assert len(extractor.requests) == 1
    assert service.list_memories(principal) == ()
    assert service.list_pending_reviews(principal) == ()


def test_pending_confirmation_and_rejection_are_owner_scoped() -> None:
    extractor = FakeCandidateExtractor(
        (
            candidate_proposal(
                "我可能偏好表格",
                content="用户可能偏好表格",
                assertion_kind=AssertionKind.SYSTEM_INFERENCE,
                expression_basis=ExpressionBasis.INFERRED,
                proposed_owner_id="analyst-b",
            ),
            candidate_proposal(
                "也许接口下周继续",
                subject="interface-refactor",
                memory_type="ongoing_item",
                content="接口可能下周继续",
                durability=CandidateDurability.UNCERTAIN,
            ),
        )
    )
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [TestScenarioPolicy()],
        candidate_extractor=extractor,
    )
    analyst_a = PrincipalContext("analyst-a")
    analyst_b = PrincipalContext("analyst-b")
    service.capture_turn(
        analyst_a,
        _turn("我可能偏好表格。也许接口下周继续。"),
    )
    reviews = service.list_pending_reviews(analyst_a)

    assert len(reviews) == 2
    assert service.list_memories(analyst_a) == ()
    assert service.list_pending_reviews(analyst_b) == ()
    with pytest.raises(ReviewNotFoundError, match="unavailable"):
        service.get_review(analyst_b, reviews[0].review_id)
    with pytest.raises(ReviewNotFoundError, match="unavailable"):
        service.confirm_review(analyst_b, reviews[0].review_id)

    memory = service.confirm_review(analyst_a, reviews[0].review_id)
    rejected = service.reject_review(analyst_a, reviews[1].review_id)

    assert memory.item.owner_id == "analyst-a"
    assert memory.current_revision.content == "用户可能偏好表格"
    assert memory.evidence[0].source_turn_id == "turn-1"
    assert rejected.status is ReviewStatus.REJECTED
    assert rejected.decided_at is not None
    assert (
        service.get_review(analyst_a, reviews[0].review_id).status
        is ReviewStatus.CONFIRMED
    )
    assert service.list_memories(analyst_a) == (memory,)
    assert service.list_pending_reviews(analyst_a) == ()


def test_sqlite_persists_owner_scoped_review_resolution(
    capture_database_path: Path,
) -> None:
    extractor = FakeCandidateExtractor(
        (
            candidate_proposal(
                "我可能偏好表格",
                content="用户可能偏好表格",
                assertion_kind=AssertionKind.SYSTEM_INFERENCE,
                expression_basis=ExpressionBasis.INFERRED,
            ),
            candidate_proposal(
                "接口也许下周继续",
                subject="interface-refactor",
                memory_type="ongoing_item",
                content="接口可能下周继续",
                durability=CandidateDurability.UNCERTAIN,
            ),
        )
    )
    repository = SQLiteMemoryRepository(connection_factory(capture_database_path))
    service = create_memory_service(
        repository,
        [TestScenarioPolicy()],
        candidate_extractor=extractor,
    )
    analyst_a = PrincipalContext("analyst-a")
    analyst_b = PrincipalContext("analyst-b")
    service.capture_turn(
        analyst_a,
        _turn("我可能偏好表格。接口也许下周继续。"),
    )
    reviews = service.list_pending_reviews(analyst_a)

    assert len(reviews) == 2
    assert service.list_pending_reviews(analyst_b) == ()
    with pytest.raises(ReviewNotFoundError):
        service.reject_review(analyst_b, reviews[0].review_id)

    confirmed = service.confirm_review(analyst_a, reviews[0].review_id)
    service.reject_review(analyst_a, reviews[1].review_id)

    reopened = create_memory_service(
        SQLiteMemoryRepository(connection_factory(capture_database_path)),
        [TestScenarioPolicy()],
    )
    assert (
        reopened.get_review(
            analyst_a,
            reviews[0].review_id,
        ).status
        is ReviewStatus.CONFIRMED
    )
    assert (
        reopened.get_review(
            analyst_a,
            reviews[1].review_id,
        ).status
        is ReviewStatus.REJECTED
    )
    assert reopened.list_memories(analyst_a) == (confirmed,)
    assert reopened.list_memories(analyst_b) == ()


def test_two_scenario_policies_supply_different_extraction_contracts() -> None:
    class RoutingExtractor(FakeCandidateExtractor):
        def extract(self, request):
            self.requests.append(request)
            memory_type = sorted(request.allowed_memory_types)[0]
            return (
                candidate_proposal(
                    request.content.rstrip("。"),
                    subject="subject",
                    memory_type=memory_type,
                    content=f"captured-{memory_type}",
                ),
            )

    extractor = RoutingExtractor()
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [TestScenarioPolicy(), AlternateScenarioPolicy()],
        candidate_extractor=extractor,
    )
    principal = PrincipalContext("analyst-a")

    service.capture_turn(
        principal,
        _turn("保存项目事项。", scenario="project-work", turn_id="turn-1"),
    )
    service.capture_turn(
        principal,
        _turn("保存个人承诺。", scenario="personal-notes", turn_id="turn-2"),
    )

    assert extractor.requests[0].policy_version == "project-work-v1"
    assert extractor.requests[1].policy_version == "personal-notes-v1"
    assert extractor.requests[0].allowed_memory_types != (
        extractor.requests[1].allowed_memory_types
    )
    assert {record.item.scenario for record in service.list_memories(principal)} == {
        "project-work",
        "personal-notes",
    }


def _turn(
    content: str,
    *,
    scenario: str = "project-work",
    turn_id: str = "turn-1",
) -> TurnEnvelope:
    return TurnEnvelope(
        scenario=scenario,
        conversation_id="conversation-1",
        source_turn_id=turn_id,
        content=content,
        observed_at=_OBSERVED_AT,
    )
