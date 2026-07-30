import logging
from datetime import UTC, datetime

import pytest

from memory_mcp.core import (
    AdmissionDecision,
    AssertionKind,
    CandidateDurability,
    CaptureStatus,
    ExpressionBasis,
    MessageRole,
    PrincipalContext,
    ReviewNotFoundError,
    ReviewStatus,
    TurnEnvelope,
    TurnMessage,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.composition import create_memory_service
from tests.support.fakes import (
    AlternateScenarioPolicy,
    FakeCandidateExtractor,
    TestScenarioPolicy,
    candidate_proposal,
)

_OBSERVED_AT = datetime(2026, 7, 29, 10, tzinfo=UTC)


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
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "secret-password-123"
    safe_expression = "项目周报默认用表格"
    extractor = FakeCandidateExtractor((candidate_proposal(safe_expression),))
    repository = InMemoryMemoryRepository()
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
    assert all(
        secret not in record.current_revision.content
        for record in service.list_memories(PrincipalContext("analyst-a"))
    )
    assert secret not in caplog.text
    assert all(not hasattr(outcome, "content") for outcome in result.outcomes)


def test_subject_hint_is_redacted_before_extraction() -> None:
    secret = "hint-secret-123"
    extractor = FakeCandidateExtractor()
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [TestScenarioPolicy()],
        candidate_extractor=extractor,
    )
    turn = TurnEnvelope(
        scenario="project-work",
        conversation_id="conversation-1",
        source_turn_id="turn-1",
        content="这是一段安全输入。",
        observed_at=_OBSERVED_AT,
        subject_hint=f"api_key={secret}",
    )

    result = service.capture_turn(PrincipalContext("analyst-a"), turn)

    assert [outcome.decision for outcome in result.outcomes] == [
        AdmissionDecision.BLOCKED
    ]
    assert extractor.requests[0].subject_hint == "[REDACTED:credential]"
    assert secret not in extractor.requests[0].subject_hint


def test_capture_is_idempotent_without_duplicate_state() -> None:
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
    repository = InMemoryMemoryRepository()
    service = create_memory_service(
        repository,
        [TestScenarioPolicy()],
        candidate_extractor=extractor,
    )

    first = service.capture_turn(principal, turn)
    second = service.capture_turn(principal, turn)

    assert first.capture_id == second.capture_id
    assert second.replayed is True
    assert second.outcomes == first.outcomes
    assert len(extractor.requests) == 1

    assert len(service.list_memories(principal)) == 1
    assert len(service.list_pending_reviews(principal)) == 1


def test_sensitive_model_output_is_blocked_before_persistence() -> None:
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
        InMemoryMemoryRepository(),
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


@pytest.mark.parametrize("sensitive_field", ("subject", "save_rationale"))
def test_sensitive_model_metadata_is_blocked_before_persistence(
    caplog: pytest.LogCaptureFixture,
    sensitive_field: str,
) -> None:
    secret = f"metadata-secret-{sensitive_field}"
    options = {sensitive_field: f"密码是 {secret}"}
    extractor = FakeCandidateExtractor(
        (
            candidate_proposal(
                "这是一段安全输入",
                **options,
            ),
        )
    )
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [TestScenarioPolicy()],
        candidate_extractor=extractor,
    )
    principal = PrincipalContext("analyst-a")

    with caplog.at_level(logging.DEBUG):
        result = service.capture_turn(principal, _turn("这是一段安全输入。"))

    assert [outcome.decision for outcome in result.outcomes] == [
        AdmissionDecision.BLOCKED
    ]
    assert service.list_memories(principal) == ()
    assert service.list_pending_reviews(principal) == ()
    assert secret not in caplog.text


def test_sensitive_source_metadata_is_blocked_before_persistence() -> None:
    source = "这是一段安全输入"
    secret = "source-metadata-secret"
    extractor = FakeCandidateExtractor((candidate_proposal(source),))
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [TestScenarioPolicy()],
        candidate_extractor=extractor,
    )
    turn = TurnEnvelope(
        scenario="project-work",
        conversation_id="conversation-1",
        source_turn_id="turn-1",
        content=f"{source}。",
        observed_at=_OBSERVED_AT,
        messages=(
            TurnMessage(
                role=MessageRole.USER,
                content=f"{source}。",
                message_id=f"api_key={secret}",
            ),
        ),
    )

    result = service.capture_turn(PrincipalContext("analyst-a"), turn)

    assert [outcome.decision for outcome in result.outcomes] == [
        AdmissionDecision.BLOCKED
    ]
    assert service.list_memories(PrincipalContext("analyst-a")) == ()


def test_backend_exception_message_is_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "backend-exception-secret"

    class FailingExtractor(FakeCandidateExtractor):
        def extract(self, request):
            self.requests.append(request)
            raise RuntimeError(f"backend included {secret}")

    service = create_memory_service(
        InMemoryMemoryRepository(),
        [TestScenarioPolicy()],
        candidate_extractor=FailingExtractor(),
    )

    with caplog.at_level(logging.ERROR):
        result = service.capture_turn(
            PrincipalContext("analyst-a"),
            _turn("这是一段安全输入。"),
        )

    assert result.status is CaptureStatus.REPROCESS_REQUIRED
    assert 'error_type="RuntimeError"' in caplog.text
    assert secret not in caplog.text


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
