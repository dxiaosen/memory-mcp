import logging
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from memory_mcp.core import (
    AdmissionDecision,
    AssertionKind,
    CandidateDurability,
    CaptureStatus,
    EvidenceSourceType,
    ExpressionBasis,
    IdempotencyConflictError,
    MemoryRelationPolicy,
    MessageRole,
    PrincipalContext,
    RelationOrigin,
    RelationProposal,
    RelationScope,
    RelationStatus,
    ReviewNotFoundError,
    ReviewStatus,
    SensitivityLevel,
    TurnEnvelope,
    TurnMessage,
    VerificationStatus,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.composition import create_memory_service

from tests.support.fakes import (
    AlternateMemoryProfile,
    FakeCandidateExtractor,
    FakeRelationExtractor,
    TestMemoryProfile,
    candidate_proposal,
    project_preference_command,
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
        [TestMemoryProfile()],
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
    assert result.metadata.profile_version == "project-work-v1"
    assert len(result.metadata.profile_fingerprint) == 64
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
    assert ongoing.current_revision.extraction_confidence == 0.95
    assert ongoing.current_revision.verification_status is VerificationStatus.UNVERIFIED
    assert ongoing.current_revision.sensitivity_level is SensitivityLevel.CONFIDENTIAL
    assert ongoing.current_revision.valid_from == _OBSERVED_AT
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
        [TestMemoryProfile()],
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
        [TestMemoryProfile()],
        candidate_extractor=extractor,
    )
    turn = TurnEnvelope(
        profile_id="project-work",
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
        [TestMemoryProfile()],
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


@pytest.mark.parametrize("with_event_id", [False, True])
def test_capture_idempotency_survives_profile_version_upgrade(
    with_event_id: bool,
) -> None:
    repository = InMemoryMemoryRepository()
    principal = PrincipalContext("analyst-a")
    turn = _turn("以后项目周报默认用表格。")
    if with_event_id:
        turn = replace(
            turn,
            event_id="event-across-profile-upgrade",
            contract_version="1",
            payload_fingerprint="unchanged-payload",
        )
    first_extractor = FakeCandidateExtractor(
        (candidate_proposal("以后项目周报默认用表格"),)
    )
    first_service = create_memory_service(
        repository,
        [TestMemoryProfile(profile_version="project-work-v1")],
        candidate_extractor=first_extractor,
    )
    first = first_service.capture_turn(principal, turn)

    upgraded_extractor = FakeCandidateExtractor(
        (candidate_proposal("以后项目周报默认用表格"),)
    )
    upgraded_service = create_memory_service(
        repository,
        [TestMemoryProfile(profile_version="project-work-v2")],
        candidate_extractor=upgraded_extractor,
    )
    replay = upgraded_service.capture_turn(principal, turn)

    assert replay.capture_id == first.capture_id
    assert replay.replayed is True
    assert replay.metadata.profile_version == "project-work-v1"
    assert upgraded_extractor.requests == []
    assert len(upgraded_service.list_memories(principal)) == 1


def test_event_id_is_owner_scoped_and_rejects_changed_payload() -> None:
    repository = InMemoryMemoryRepository()
    service = create_memory_service(
        repository,
        [TestMemoryProfile()],
        candidate_extractor=FakeCandidateExtractor(),
    )
    turn = replace(
        _turn("这是稳定输入。"),
        event_id="shared-event-id",
        contract_version="1",
        payload_fingerprint="payload-a",
    )

    owner_a = service.capture_turn(PrincipalContext("analyst-a"), turn)
    owner_b = service.capture_turn(PrincipalContext("analyst-b"), turn)
    with pytest.raises(IdempotencyConflictError):
        service.capture_turn(
            PrincipalContext("analyst-a"),
            replace(turn, payload_fingerprint="payload-changed"),
        )

    assert owner_a.capture_id != owner_b.capture_id


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
        [TestMemoryProfile()],
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
        [TestMemoryProfile()],
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
        [TestMemoryProfile()],
        candidate_extractor=extractor,
    )
    turn = TurnEnvelope(
        profile_id="project-work",
        conversation_id="conversation-1",
        source_turn_id="turn-1",
        content=f"{source}。",
        observed_at=_OBSERVED_AT,
        messages=(
            TurnMessage(
                role=MessageRole.USER,
                content=f"{source}。",
                source_uri=f"https://example.invalid/?api_key={secret}",
            ),
        ),
    )

    result = service.capture_turn(PrincipalContext("analyst-a"), turn)

    assert [outcome.decision for outcome in result.outcomes] == [
        AdmissionDecision.BLOCKED
    ]
    assert service.list_memories(PrincipalContext("analyst-a")) == ()


def test_tool_document_source_is_preserved_after_user_confirmation() -> None:
    source_expression = "示例公司 2025 年收入同比增长 18%"
    published_at = datetime(2026, 3, 20, 9, tzinfo=UTC)
    retrieved_at = datetime(2026, 7, 29, 9, tzinfo=UTC)
    extractor = FakeCandidateExtractor(
        (
            candidate_proposal(
                source_expression,
                subject="example-company-revenue",
                memory_type="stable_context",
                content="示例公司 2025 年收入同比增长 18%",
                assertion_kind=AssertionKind.EXTERNAL_FACT,
            ),
        )
    )
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [TestMemoryProfile()],
        candidate_extractor=extractor,
    )
    principal = PrincipalContext("analyst-a")
    turn = TurnEnvelope(
        profile_id="project-work",
        conversation_id="conversation-1",
        source_turn_id="turn-document-1",
        content=source_expression,
        observed_at=_OBSERVED_AT,
        messages=(
            TurnMessage(
                role=MessageRole.TOOL,
                content=source_expression,
                message_id="tool-message-1",
                tool_name="document_reader",
                source_type=EvidenceSourceType.DOCUMENT,
                source_uri="https://research.example/reports/annual-2025",
                source_title="示例公司 2025 年报",
                source_publisher="示例交易所",
                published_at=published_at,
                retrieved_at=retrieved_at,
                content_hash="sha256:example-report",
                citation_locator="p.42",
            ),
        ),
    )

    capture = service.capture_turn(principal, turn)

    assert capture.outcomes[0].decision is AdmissionDecision.PENDING
    review_id = capture.outcomes[0].review_id
    assert review_id is not None
    pending = service.get_review(principal, review_id)
    assert pending.candidate.verification_status is VerificationStatus.UNVERIFIED

    memory = service.confirm_review(principal, review_id)
    source = memory.evidence[0]
    assert (
        memory.current_revision.verification_status is VerificationStatus.USER_CONFIRMED
    )
    assert source.source_type is EvidenceSourceType.DOCUMENT
    assert source.document is not None
    assert source.document.source_uri == "https://research.example/reports/annual-2025"
    assert source.document.source_title == "示例公司 2025 年报"
    assert source.document.source_publisher == "示例交易所"
    assert source.document.published_at == published_at
    assert source.document.retrieved_at == retrieved_at
    assert source.document.content_hash == "sha256:example-report"
    assert source.document.citation_locator == "p.42"


def test_backend_exception_message_is_logged_for_debugging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # 开发阶段：后端异常的类型与消息均需记录以便排障（用户已明确放开安全约束）。
    marker = "backend-exception-marker"

    class FailingExtractor(FakeCandidateExtractor):
        def extract(self, request):
            self.requests.append(request)
            raise RuntimeError(f"backend included {marker}")

    service = create_memory_service(
        InMemoryMemoryRepository(),
        [TestMemoryProfile()],
        candidate_extractor=FailingExtractor(),
    )

    with caplog.at_level(logging.ERROR):
        result = service.capture_turn(
            PrincipalContext("analyst-a"),
            _turn("这是一段安全输入。"),
        )

    assert result.status is CaptureStatus.REPROCESS_REQUIRED
    assert result.failure_code == "processing_interrupted"
    assert 'event="memory.capture.processing_failed"' in caplog.text
    assert 'error_type="RuntimeError"' in caplog.text
    assert f'error_message="backend included {marker}"' in caplog.text


def test_retryable_failure_is_reprocessed_without_duplicates() -> None:
    extractor = FakeCandidateExtractor(
        (candidate_proposal("以后项目周报默认用表格"),),
        failures_before_success=1,
    )
    repository = InMemoryMemoryRepository()
    service = create_memory_service(
        repository,
        [TestMemoryProfile(profile_version="project-work-v1")],
        candidate_extractor=extractor,
    )
    principal = PrincipalContext("analyst-a")
    turn = _turn("以后项目周报默认用表格。")

    failed = service.capture_turn(principal, turn)
    upgraded = create_memory_service(
        repository,
        [
            TestMemoryProfile(
                profile_version="project-work-v2",
                capture_guidance="Capture durable project-work context, version 2.",
            )
        ],
        candidate_extractor=extractor,
    )
    completed = upgraded.capture_turn(principal, turn)
    replayed = upgraded.capture_turn(principal, turn)

    assert failed.status is CaptureStatus.REPROCESS_REQUIRED
    assert failed.failure_code == "processing_interrupted"
    assert completed.status is CaptureStatus.COMPLETED
    assert completed.capture_id == failed.capture_id
    assert completed.was_reprocessed is True
    assert completed.metadata.profile_version == "project-work-v2"
    assert completed.metadata.profile_fingerprint != failed.metadata.profile_fingerprint
    assert replayed.replayed is True
    assert len(extractor.requests) == 2
    assert len(upgraded.list_memories(principal)) == 1


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
        [TestMemoryProfile()],
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
        [TestMemoryProfile()],
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


def test_two_profile_profiles_supply_different_extraction_contracts() -> None:
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
        [TestMemoryProfile(), AlternateMemoryProfile()],
        candidate_extractor=extractor,
    )
    principal = PrincipalContext("analyst-a")

    service.capture_turn(
        principal,
        _turn("保存项目事项。", profile_id="project-work", turn_id="turn-1"),
    )
    service.capture_turn(
        principal,
        _turn("保存个人承诺。", profile_id="personal-notes", turn_id="turn-2"),
    )

    assert extractor.requests[0].profile_version == "project-work-v1"
    assert extractor.requests[1].profile_version == "personal-notes-v1"
    assert extractor.requests[0].allowed_memory_types != (
        extractor.requests[1].allowed_memory_types
    )
    assert {record.item.profile_id for record in service.list_memories(principal)} == {
        "project-work",
        "personal-notes",
    }


def test_automatic_relation_links_same_capture_memories_and_replays_once() -> None:
    text = "周报格式偏好明确支持接口重构跟进"
    candidate_extractor = FakeCandidateExtractor(
        (
            candidate_proposal(
                "周报格式偏好",
                subject="weekly-report",
                memory_type="preference",
            ),
            candidate_proposal(
                "接口重构跟进",
                subject="interface-refactor",
                memory_type="ongoing_item",
                content="继续跟进接口重构",
            ),
        )
    )
    relation_extractor = FakeRelationExtractor(
        lambda request: (_relation_proposal(request, text),)
    )
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [_relation_profile()],
        candidate_extractor=candidate_extractor,
        relation_extractor=relation_extractor,
    )
    principal = PrincipalContext("analyst-a")

    first = service.capture_turn(principal, _turn(text))
    replayed = service.capture_turn(principal, _turn(text))
    candidate_extractor.proposals = ()
    duplicate = service.capture_turn(principal, _turn(text, turn_id="turn-2"))

    memories = service.list_memories(principal)
    source = next(
        record for record in memories if record.item.memory_type == "preference"
    )
    relations = service.list_memory_relations(principal, source.item.memory_id)
    assert first.status is CaptureStatus.COMPLETED
    assert replayed.replayed is True
    assert duplicate.status is CaptureStatus.COMPLETED
    assert len(relations) == 1
    relation = relations[0].relation
    assert relation.relation_type == "supports"
    assert relation.origin is RelationOrigin.AUTOMATIC
    assert relation.scope is RelationScope.REVISION
    assert relation.source_revision_id == source.current_revision.revision_id
    assert relation.provenance is not None
    assert relation.provenance.capture_id == first.capture_id
    assert relation.provenance.conversation_id == "conversation-1"
    assert relation.provenance.source_turn_id == "turn-1"
    assert relation.provenance.source_expression == text
    assert relation.provenance.confidence == 0.96
    assert relation.provenance.model_id == "fake-relation-model"
    assert len(candidate_extractor.requests) == 2
    assert len(relation_extractor.requests) == 2


def test_relation_free_profile_skips_relation_model() -> None:
    relation_extractor = FakeRelationExtractor()
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [TestMemoryProfile()],
        candidate_extractor=FakeCandidateExtractor(
            (candidate_proposal("以后周报默认用表格"),)
        ),
        relation_extractor=relation_extractor,
    )

    result = service.capture_turn(
        PrincipalContext("analyst-a"),
        _turn("以后周报默认用表格"),
    )

    assert result.status is CaptureStatus.COMPLETED
    assert relation_extractor.requests == []


def test_automatic_relation_becomes_stale_and_can_be_recreated() -> None:
    first_text = "周报偏好明确支持持续事项"
    candidate_extractor = _two_relation_candidates()
    relation_extractor = FakeRelationExtractor(
        lambda request: (_relation_proposal(request, first_text),)
    )
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [_relation_profile()],
        candidate_extractor=candidate_extractor,
        relation_extractor=relation_extractor,
    )
    principal = PrincipalContext("analyst-a")

    service.capture_turn(principal, _turn(first_text))
    source = next(
        record
        for record in service.list_memories(principal)
        if record.item.memory_type == "preference"
    )
    original_relation = service.list_memory_relations(
        principal,
        source.item.memory_id,
    )[0].relation

    replacement_text = "以后项目周报改为图表"
    candidate_extractor.proposals = (
        candidate_proposal(
            replacement_text,
            memory_type="preference",
            content="项目周报默认使用图表",
        ),
    )
    relation_extractor.proposal_factory = lambda request: ()
    service.capture_turn(
        principal,
        TurnEnvelope(
            profile_id="project-work",
            conversation_id="conversation-1",
            source_turn_id="turn-replacement",
            content=replacement_text,
            observed_at=_OBSERVED_AT,
            messages=(
                TurnMessage(
                    role=MessageRole.USER,
                    content=replacement_text,
                    message_id="replacement-message",
                ),
            ),
        ),
    )

    assert service.list_memory_relations(principal, source.item.memory_id) == ()
    stale = service.list_memory_relations(
        principal,
        source.item.memory_id,
        include_inactive=True,
    )[0].relation
    assert stale.relation_id == original_relation.relation_id
    assert stale.status is RelationStatus.STALE
    assert stale.stale_at is not None
    assert stale.stale_reason == "endpoint_revision_changed"

    rebuilt_text = "图表周报偏好明确支持持续事项"
    candidate_extractor.proposals = ()
    relation_extractor.proposal_factory = lambda request: (
        _relation_proposal(request, rebuilt_text),
    )
    service.capture_turn(
        principal,
        TurnEnvelope(
            profile_id="project-work",
            conversation_id="conversation-1",
            source_turn_id="turn-rebuild-relation",
            content=rebuilt_text,
            observed_at=_OBSERVED_AT,
            messages=(
                TurnMessage(
                    role=MessageRole.USER,
                    content=rebuilt_text,
                    message_id="rebuilt-relation-message",
                ),
            ),
        ),
    )

    active = service.list_memory_relations(principal, source.item.memory_id)
    history = service.list_memory_relations(
        principal,
        source.item.memory_id,
        include_inactive=True,
    )
    current_source = service.get_memory(principal, source.item.memory_id)
    assert len(active) == 1
    assert active[0].relation.relation_id != original_relation.relation_id
    assert active[0].relation.source_revision_id == (
        current_source.current_revision.revision_id
    )
    assert {summary.relation.status for summary in history} == {
        RelationStatus.ACTIVE,
        RelationStatus.STALE,
    }

    revoked = service.revoke_memory_relation(principal, stale.relation_id)
    assert revoked.status is RelationStatus.REVOKED
    assert revoked.stale_at == stale.stale_at
    assert revoked.stale_reason == "endpoint_revision_changed"


def test_automatic_relation_catalog_excludes_pending_and_other_owner() -> None:
    profile = _relation_profile()
    repository = InMemoryMemoryRepository()
    relation_extractor = FakeRelationExtractor()
    service = create_memory_service(
        repository,
        [profile],
        candidate_extractor=FakeCandidateExtractor(
            (
                candidate_proposal("周报偏好", memory_type="preference"),
                candidate_proposal(
                    "可能继续另一个事项",
                    subject="pending-target",
                    memory_type="ongoing_item",
                    content="可能继续另一个事项",
                    assertion_kind=AssertionKind.SYSTEM_INFERENCE,
                    expression_basis=ExpressionBasis.INFERRED,
                ),
            )
        ),
        relation_extractor=relation_extractor,
    )
    analyst_a = PrincipalContext("analyst-a")
    analyst_b = PrincipalContext("analyst-b")
    existing = service.create_memory(
        analyst_a,
        replace(
            project_preference_command(),
            subject="existing-target",
            memory_type="ongoing_item",
            content="继续已有事项",
            source_turn_id="existing-turn-a",
            source_expression="继续已有事项",
        ),
    )
    other_owner = service.create_memory(
        analyst_b,
        replace(
            project_preference_command(),
            subject="other-owner-target",
            memory_type="ongoing_item",
            content="其他用户事项",
            source_turn_id="existing-turn-b",
            source_expression="其他用户事项",
        ),
    )

    service.capture_turn(
        analyst_a,
        _turn("周报偏好明确支持已有事项。可能继续另一个事项"),
    )

    request = relation_extractor.requests[0]
    endpoint_ids = {endpoint.memory_id for endpoint in request.endpoints}
    endpoint_subjects = {endpoint.subject for endpoint in request.endpoints}
    assert existing.item.memory_id in endpoint_ids
    assert other_owner.item.memory_id not in endpoint_ids
    assert "pending-target" not in endpoint_subjects


def test_relation_catalog_is_bounded_and_prioritizes_same_capture_memory() -> None:
    profile = _relation_profile()
    relation_extractor = FakeRelationExtractor()
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [profile],
        candidate_extractor=FakeCandidateExtractor(
            (candidate_proposal("本轮周报偏好", memory_type="preference"),)
        ),
        relation_extractor=relation_extractor,
    )
    principal = PrincipalContext("analyst-a")
    for index in range(45):
        service.create_memory(
            principal,
            replace(
                project_preference_command(),
                subject=f"existing-task-{index}",
                memory_type="ongoing_item",
                content=f"已有事项 {index}",
                source_turn_id=f"existing-turn-{index}",
                source_expression=f"已有事项 {index}",
            ),
        )

    service.capture_turn(principal, _turn("本轮周报偏好支持已有事项"))

    endpoints = relation_extractor.requests[0].endpoints
    assert len(endpoints) == 40
    assert endpoints[0].memory_type == "preference"
    assert endpoints[0].subject == "weekly-report"


def test_replacement_revision_is_prioritized_as_relation_endpoint() -> None:
    text = "周报偏好改成图表并明确支持持续事项"
    relation_extractor = FakeRelationExtractor(
        lambda request: (_relation_proposal(request, text),)
    )
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [_relation_profile()],
        candidate_extractor=FakeCandidateExtractor(
            (
                candidate_proposal(
                    text,
                    memory_type="preference",
                    content="项目周报默认使用图表",
                ),
            )
        ),
        relation_extractor=relation_extractor,
    )
    principal = PrincipalContext("analyst-a")
    source = service.create_memory(principal, project_preference_command())
    service.create_memory(
        principal,
        replace(
            project_preference_command(),
            subject="continued-work",
            memory_type="ongoing_item",
            content="继续持续事项",
            source_turn_id="continued-turn",
            source_expression="继续持续事项",
        ),
    )

    result = service.capture_turn(
        principal,
        TurnEnvelope(
            profile_id="project-work",
            conversation_id="conversation-1",
            source_turn_id="turn-replacement-relation",
            content=text,
            observed_at=_OBSERVED_AT,
            messages=(
                TurnMessage(
                    role=MessageRole.USER,
                    content=text,
                    message_id="replacement-user-message",
                ),
            ),
        ),
    )

    assert result.status is CaptureStatus.COMPLETED
    request_source = next(
        endpoint
        for endpoint in relation_extractor.requests[0].endpoints
        if endpoint.memory_id == source.item.memory_id
    )
    assert request_source.content == "项目周报默认使用图表"
    assert (
        service.get_memory(principal, source.item.memory_id).current_revision.content
        == "项目周报默认使用图表"
    )
    assert len(service.list_memory_relations(principal, source.item.memory_id)) == 1


def test_relation_admission_is_explicit_high_confidence_and_deduplicated() -> None:
    text = "周报偏好明确支持持续事项"

    def proposals(request):
        accepted = _relation_proposal(request, text)
        return (
            accepted,
            accepted,
            replace(accepted, confidence=0.89),
            replace(accepted, expression_basis=ExpressionBasis.INFERRED),
        )

    service = create_memory_service(
        InMemoryMemoryRepository(),
        [_relation_profile()],
        candidate_extractor=_two_relation_candidates(),
        relation_extractor=FakeRelationExtractor(proposals),
    )
    principal = PrincipalContext("analyst-a")

    service.capture_turn(principal, _turn(text))

    source = next(
        record
        for record in service.list_memories(principal)
        if record.item.memory_type == "preference"
    )
    assert len(service.list_memory_relations(principal, source.item.memory_id)) == 1


def test_untrusted_relation_evidence_is_not_auto_saved() -> None:
    cases = (
        ("周报偏好不能支持持续事项", "周报偏好不能支持持续事项"),
        ("周报偏好明确支持持续事项", "支持"),
        ("持续事项明确支持周报偏好", "持续事项明确支持周报偏好"),
    )

    for text, source_expression in cases:
        service = create_memory_service(
            InMemoryMemoryRepository(),
            [_relation_profile()],
            candidate_extractor=_two_relation_candidates(),
            relation_extractor=FakeRelationExtractor(
                lambda request, expression=source_expression: (
                    _relation_proposal(request, expression),
                )
            ),
        )
        principal = PrincipalContext("analyst-a")

        result = service.capture_turn(principal, _turn(text))

        source = next(
            record
            for record in service.list_memories(principal)
            if record.item.memory_type == "preference"
        )
        assert result.status is CaptureStatus.COMPLETED, text
        assert service.list_memory_relations(principal, source.item.memory_id) == (), (
            text
        )


def test_assistant_only_relation_expression_is_not_auto_saved() -> None:
    relation_text = "周报偏好明确支持持续事项"
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [_relation_profile()],
        candidate_extractor=_two_relation_candidates(),
        relation_extractor=FakeRelationExtractor(
            lambda request: (_relation_proposal(request, relation_text),)
        ),
    )
    principal = PrincipalContext("analyst-a")
    user_text = "周报偏好。持续事项。"
    turn = TurnEnvelope(
        profile_id="project-work",
        conversation_id="conversation-1",
        source_turn_id="turn-assistant-relation",
        content=f"[user]\n{user_text}\n[assistant]\n{relation_text}",
        observed_at=_OBSERVED_AT,
        messages=(
            TurnMessage(
                role=MessageRole.USER,
                content=user_text,
                message_id="user-message",
            ),
            TurnMessage(
                role=MessageRole.ASSISTANT,
                content=relation_text,
                message_id="assistant-message",
            ),
        ),
    )

    result = service.capture_turn(principal, turn)

    assert result.status is CaptureStatus.COMPLETED
    source = next(
        record
        for record in service.list_memories(principal)
        if record.item.memory_type == "preference"
    )
    assert service.list_memory_relations(principal, source.item.memory_id) == ()


def test_unknown_relation_endpoint_fails_without_persisting_candidates() -> None:
    text = "周报偏好明确支持持续事项"

    def proposals(request):
        return (
            replace(
                _relation_proposal(request, text),
                target_memory_id=uuid4(),
            ),
        )

    service = create_memory_service(
        InMemoryMemoryRepository(),
        [_relation_profile()],
        candidate_extractor=_two_relation_candidates(),
        relation_extractor=FakeRelationExtractor(proposals),
    )
    principal = PrincipalContext("analyst-a")

    result = service.capture_turn(principal, _turn(text))

    assert result.status is CaptureStatus.FAILED
    assert result.failure_code == "invalid_candidate_output"
    assert service.list_memories(principal) == ()


def test_invalid_automatic_relation_direction_fails_without_writes() -> None:
    text = "周报偏好明确支持持续事项"

    def proposals(request):
        proposal = _relation_proposal(request, text)
        return (
            replace(
                proposal,
                source_memory_id=proposal.target_memory_id,
                target_memory_id=proposal.source_memory_id,
            ),
        )

    service = create_memory_service(
        InMemoryMemoryRepository(),
        [_relation_profile()],
        candidate_extractor=_two_relation_candidates(),
        relation_extractor=FakeRelationExtractor(proposals),
    )
    principal = PrincipalContext("analyst-a")

    result = service.capture_turn(principal, _turn(text))

    assert result.status is CaptureStatus.FAILED
    assert result.failure_code == "invalid_candidate_output"
    assert service.list_memories(principal) == ()


def test_relation_provider_failure_reprocesses_without_duplicate_relation() -> None:
    text = "周报偏好明确支持持续事项"
    relation_extractor = FakeRelationExtractor(
        lambda request: (_relation_proposal(request, text),),
        failures_before_success=1,
    )
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [_relation_profile()],
        candidate_extractor=_two_relation_candidates(),
        relation_extractor=relation_extractor,
    )
    principal = PrincipalContext("analyst-a")

    failed = service.capture_turn(principal, _turn(text))
    completed = service.capture_turn(principal, _turn(text))
    replayed = service.capture_turn(principal, _turn(text))

    assert failed.status is CaptureStatus.REPROCESS_REQUIRED
    assert completed.status is CaptureStatus.COMPLETED
    assert replayed.replayed is True
    assert len(relation_extractor.requests) == 2
    source = next(
        record
        for record in service.list_memories(principal)
        if record.item.memory_type == "preference"
    )
    assert len(service.list_memory_relations(principal, source.item.memory_id)) == 1


def test_in_memory_capture_rolls_back_when_relation_write_is_invalid() -> None:
    class RejectingRelationRepository(InMemoryMemoryRepository):
        def commit_capture(self, principal, write):
            if write.relations:
                broken = replace(
                    write.relations[0],
                    target_memory_id=uuid4(),
                )
                write = replace(write, relations=(broken,))
            return super().commit_capture(principal, write)

    repository = RejectingRelationRepository()
    service = create_memory_service(
        repository,
        [_relation_profile()],
        candidate_extractor=_two_relation_candidates(),
        relation_extractor=FakeRelationExtractor(
            lambda request: (
                _relation_proposal(
                    request,
                    "周报偏好明确支持持续事项",
                ),
            )
        ),
    )
    principal = PrincipalContext("analyst-a")

    with pytest.raises(ValueError, match="endpoints are unavailable"):
        service.capture_turn(
            principal,
            _turn("周报偏好明确支持持续事项"),
        )

    assert repository.list(principal, active_only=False) == ()


def test_replacement_and_stale_transition_roll_back_together() -> None:
    class RejectingReplacementRepository(InMemoryMemoryRepository):
        def commit_capture(self, principal, write):
            if write.replacements and write.relations:
                broken = replace(
                    write.relations[0],
                    target_memory_id=uuid4(),
                )
                write = replace(write, relations=(broken,))
            return super().commit_capture(principal, write)

    repository = RejectingReplacementRepository()
    candidate_extractor = _two_relation_candidates()
    first_text = "周报偏好明确支持持续事项"
    relation_extractor = FakeRelationExtractor(
        lambda request: (_relation_proposal(request, first_text),)
    )
    service = create_memory_service(
        repository,
        [_relation_profile()],
        candidate_extractor=candidate_extractor,
        relation_extractor=relation_extractor,
    )
    principal = PrincipalContext("analyst-a")
    service.capture_turn(principal, _turn(first_text))
    source = next(
        record
        for record in service.list_memories(principal)
        if record.item.memory_type == "preference"
    )
    active_relation = service.list_memory_relations(
        principal,
        source.item.memory_id,
    )[0].relation

    replacement_text = "以后项目周报改为图表并明确支持持续事项"
    candidate_extractor.proposals = (
        candidate_proposal(
            replacement_text,
            memory_type="preference",
            content="项目周报默认使用图表",
        ),
    )
    relation_extractor.proposal_factory = lambda request: (
        _relation_proposal(request, replacement_text),
    )

    with pytest.raises(ValueError, match="endpoints are unavailable"):
        service.capture_turn(
            principal,
            TurnEnvelope(
                profile_id="project-work",
                conversation_id="conversation-1",
                source_turn_id="turn-rollback-replacement",
                content=replacement_text,
                observed_at=_OBSERVED_AT,
                messages=(
                    TurnMessage(
                        role=MessageRole.USER,
                        content=replacement_text,
                        message_id="rollback-replacement-message",
                    ),
                ),
            ),
        )

    unchanged = service.get_memory(principal, source.item.memory_id)
    relation = service.list_memory_relations(
        principal,
        source.item.memory_id,
    )[0].relation
    assert unchanged.current_revision.revision_number == 1
    assert relation.relation_id == active_relation.relation_id
    assert relation.status is RelationStatus.ACTIVE


def _relation_profile():
    return replace(
        TestMemoryProfile(),
        relation_policies={
            "supports": MemoryRelationPolicy(
                source_memory_types=frozenset({"preference"}),
                target_memory_types=frozenset({"ongoing_item"}),
                description="A preference supports an ongoing item.",
                direction_cues=frozenset({"支持"}),
            )
        },
    )


def _two_relation_candidates() -> FakeCandidateExtractor:
    return FakeCandidateExtractor(
        (
            candidate_proposal("周报偏好", memory_type="preference"),
            candidate_proposal(
                "持续事项",
                subject="continued-work",
                memory_type="ongoing_item",
                content="继续持续事项",
            ),
        )
    )


def _relation_proposal(request, source_expression: str) -> RelationProposal:
    source = next(
        endpoint
        for endpoint in request.endpoints
        if endpoint.memory_type == "preference"
    )
    target = next(
        endpoint
        for endpoint in request.endpoints
        if endpoint.memory_type == "ongoing_item"
    )
    return RelationProposal(
        source_memory_id=source.memory_id,
        target_memory_id=target.memory_id,
        relation_type="supports",
        source_expression=source_expression,
        confidence=0.96,
        expression_basis=ExpressionBasis.EXPLICIT,
    )


def _turn(
    content: str,
    *,
    profile_id: str = "project-work",
    turn_id: str = "turn-1",
) -> TurnEnvelope:
    return TurnEnvelope(
        profile_id=profile_id,
        conversation_id="conversation-1",
        source_turn_id=turn_id,
        content=content,
        observed_at=_OBSERVED_AT,
    )
