from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from memory_mcp.core import (
    InvalidMemoryProfileError,
    InvalidMemoryRelationError,
    InvalidMemoryTypeError,
    InvalidProfileProgressError,
    LifecycleStatus,
    MemoryMetadataPolicy,
    MemoryNotFoundError,
    MemoryRelationNotFoundError,
    MemoryRelationPolicy,
    MemoryService,
    PrincipalContext,
    ProfileNotRegisteredError,
    ProfileRegistry,
    RecallQuery,
    RelationDirection,
    RelationOrigin,
    RelationScope,
    RelationStatus,
    SensitiveContentBlockedError,
    SensitivityLevel,
    VerificationStatus,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.adapters.sensitive import RegexSensitiveContentGuard
from memory_mcp.core.composition import create_memory_service

from tests.support.fakes import (
    AlternateMemoryProfile,
    FakeCandidateExtractor,
    TestMemoryProfile,
    candidate_proposal,
    project_preference_command,
)


def _service():
    return create_memory_service(
        InMemoryMemoryRepository(),
        [TestMemoryProfile()],
    )


def _relation_profile() -> TestMemoryProfile:
    return replace(
        TestMemoryProfile(),
        relation_policies={
            "supports": MemoryRelationPolicy(
                source_memory_types=frozenset({"preference"}),
                target_memory_types=frozenset({"ongoing_item"}),
                description="A durable preference supports an ongoing item.",
            )
        },
    )


def _ongoing_command():
    return replace(
        project_preference_command(),
        subject="quarterly-model",
        memory_type="ongoing_item",
        content="季度模型需要持续更新",
        source_turn_id="session-1-turn-2",
        source_expression="季度模型需要持续更新",
    )


def test_manual_create_preserves_owner_source_kind_and_current_state() -> None:
    service = _service()
    principal = PrincipalContext("analyst-a")

    record = service.create_memory(principal, project_preference_command())

    assert isinstance(record.item.memory_id, UUID)
    assert record.item.owner_id == "analyst-a"
    assert record.item.profile_id == "project-work"
    assert record.item.memory_type == "preference"
    assert record.current_revision.content == "项目周报默认使用表格"
    assert record.current_revision.lifecycle_status is LifecycleStatus.ACTIVE
    assert record.current_revision.extraction_confidence is None
    assert (
        record.current_revision.verification_status is VerificationStatus.USER_ASSERTED
    )
    assert record.current_revision.sensitivity_level is SensitivityLevel.CONFIDENTIAL
    assert record.current_revision.valid_from == record.current_revision.observed_at
    assert record.current_revision.valid_until is None
    assert record.current_revision.save_rationale
    assert record.evidence[0].source_turn_id == "session-1-turn-1"
    assert record.evidence[0].source_expression == "以后项目周报默认用表格"
    assert record.evidence[0].owner_id == "analyst-a"


def test_cross_user_identifier_is_indistinguishable_from_missing_memory() -> None:
    service = _service()
    analyst_a = PrincipalContext("analyst-a")
    analyst_b = PrincipalContext("analyst-b")
    record = service.create_memory(analyst_a, project_preference_command())

    with pytest.raises(MemoryNotFoundError, match="unavailable") as cross_user:
        service.get_memory(analyst_b, record.item.memory_id)
    with pytest.raises(MemoryNotFoundError, match="unavailable") as missing:
        service.get_memory(
            analyst_b,
            UUID("00000000-0000-0000-0000-000000000000"),
        )

    assert str(cross_user.value) == str(missing.value)
    assert service.list_memories(analyst_b) == ()


def test_all_reads_are_scoped_to_the_trusted_principal() -> None:
    service = _service()
    analyst_a = PrincipalContext("analyst-a")
    analyst_b = PrincipalContext("analyst-b")
    service.create_memory(analyst_a, project_preference_command())
    service.create_memory(analyst_b, project_preference_command())

    a_records = service.list_memories(analyst_a)
    b_records = service.list_memories(analyst_b)

    assert {record.item.owner_id for record in a_records} == {"analyst-a"}
    assert {record.item.owner_id for record in b_records} == {"analyst-b"}
    assert a_records[0].item.memory_id != b_records[0].item.memory_id


def test_repository_rejects_record_owned_by_a_different_principal() -> None:
    source_service = _service()
    analyst_a = PrincipalContext("analyst-a")
    analyst_b = PrincipalContext("analyst-b")
    record = source_service.create_memory(
        analyst_a,
        project_preference_command(),
    )
    repository = InMemoryMemoryRepository()
    repository.register_profile(TestMemoryProfile())

    with pytest.raises(ValueError, match="trusted principal"):
        repository.add(analyst_b, record)

    assert repository.get(analyst_a, record.item.memory_id) is None


def test_current_list_excludes_inactive_memory_but_history_can_include_it() -> None:
    service = _service()
    principal = PrincipalContext("analyst-a")
    service.create_memory(
        principal,
        project_preference_command(lifecycle_status=LifecycleStatus.SUPERSEDED),
    )

    assert service.list_memories(principal) == ()
    history = service.list_memories(principal, include_inactive=True)
    assert len(history) == 1
    assert history[0].current_revision.lifecycle_status is LifecycleStatus.SUPERSEDED


def test_unregistered_profile_and_invalid_type_fail_safely() -> None:
    service = _service()
    principal = PrincipalContext("analyst-a")
    unregistered = project_preference_command()
    object.__setattr__(unregistered, "profile_id", "missing")

    with pytest.raises(ProfileNotRegisteredError):
        service.create_memory(principal, unregistered)

    invalid_type = project_preference_command()
    object.__setattr__(invalid_type, "memory_type", "investment_hypothesis")
    with pytest.raises(InvalidMemoryTypeError):
        service.create_memory(principal, invalid_type)


def test_invalid_business_progress_is_rejected_by_profile() -> None:
    service = _service()
    principal = PrincipalContext("analyst-a")

    with pytest.raises(InvalidProfileProgressError):
        service.create_memory(
            principal,
            project_preference_command(business_progress="unsupported"),
        )


def test_manual_create_cannot_bypass_sensitive_persistence_guard() -> None:
    service = _service()
    principal = PrincipalContext("analyst-a")
    command = project_preference_command()
    object.__setattr__(command, "content", "密码是 manual-secret-789")

    with pytest.raises(SensitiveContentBlockedError, match="prohibited"):
        service.create_memory(principal, command)

    assert service.list_memories(principal) == ()


def test_malformed_profile_policy_is_rejected_before_use() -> None:
    service = create_memory_service(InMemoryMemoryRepository(), [])

    with pytest.raises(InvalidMemoryProfileError):
        service.register_profile(TestMemoryProfile(profile_id=" project-work "))

    with pytest.raises(InvalidMemoryProfileError, match="metadata_policies"):
        service.register_profile(TestMemoryProfile(metadata_policies={}))

    with pytest.raises(InvalidMemoryProfileError, match="recall_priorities"):
        service.register_profile(TestMemoryProfile(recall_priorities={}))

    with pytest.raises(InvalidMemoryProfileError, match="recall_hints"):
        service.register_profile(TestMemoryProfile(recall_hints={}))

    invalid_relation = MemoryRelationPolicy(
        source_memory_types=frozenset({"unknown"}),
        target_memory_types=frozenset({"preference"}),
        description="Invalid endpoint vocabulary.",
    )
    with pytest.raises(InvalidMemoryProfileError, match="unknown memory types"):
        service.register_profile(
            TestMemoryProfile(relation_policies={"supports": invalid_relation})
        )


def test_revision_metadata_invariants_reject_invalid_values() -> None:
    record = _service().create_memory(
        PrincipalContext("analyst-a"),
        project_preference_command(),
    )

    with pytest.raises(ValueError, match="extraction_confidence"):
        replace(record.current_revision, extraction_confidence=1.01)
    with pytest.raises(ValueError, match="valid_until"):
        replace(
            record.current_revision,
            valid_until=record.current_revision.valid_from,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(record.current_revision, valid_from=datetime(2026, 7, 29, 10))


def test_profile_validity_policy_filters_expired_memory_without_scheduler() -> None:
    observed_at = datetime(2026, 7, 29, 10, tzinfo=UTC)
    current_time = [observed_at]
    policies = {
        memory_type: MemoryMetadataPolicy(
            sensitivity_level=SensitivityLevel.INTERNAL,
            validity_days=(1 if memory_type == "preference" else None),
        )
        for memory_type in ("preference", "ongoing_item", "stable_context")
    }
    repository = InMemoryMemoryRepository()
    service = MemoryService(
        repository,
        ProfileRegistry(),
        sensitive_guard=RegexSensitiveContentGuard(),
        clock=lambda: current_time[0],
    )
    service.register_profile(TestMemoryProfile(metadata_policies=policies))
    principal = PrincipalContext("analyst-a")

    record = service.create_memory(principal, project_preference_command())

    assert record.current_revision.valid_until == observed_at + timedelta(days=1)
    assert record.current_revision.sensitivity_level is SensitivityLevel.INTERNAL
    assert service.list_memories(principal) == (record,)

    current_time[0] = observed_at + timedelta(days=1)

    assert service.list_memories(principal) == ()
    assert service.list_memories(principal, include_inactive=True) == (record,)
    assert (
        service.recall_memory(
            principal,
            RecallQuery(
                profile_id="project-work",
                query="项目周报默认使用表格",
            ),
        ).items
        == ()
    )


def test_repository_registration_failure_does_not_mutate_registry() -> None:
    class FailingProfileRepository(InMemoryMemoryRepository):
        def register_profile(self, profile: TestMemoryProfile) -> None:
            raise RuntimeError("database unavailable")

    registry = ProfileRegistry()
    service = MemoryService(
        FailingProfileRepository(),
        registry,
        sensitive_guard=RegexSensitiveContentGuard(),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        service.register_profile(TestMemoryProfile())

    assert registry.profile_ids == frozenset()


def test_record_without_source_evidence_is_invalid() -> None:
    service = _service()
    record = service.create_memory(
        PrincipalContext("analyst-a"),
        project_preference_command(),
    )

    with pytest.raises(ValueError, match="source evidence"):
        replace(record, evidence=())


def test_relation_link_replay_direction_revoke_and_owner_isolation() -> None:
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [_relation_profile()],
    )
    owner_a = PrincipalContext("analyst-a")
    owner_b = PrincipalContext("analyst-b")
    source = service.create_memory(owner_a, project_preference_command())
    target = service.create_memory(owner_a, _ongoing_command())
    foreign = service.create_memory(owner_b, _ongoing_command())

    relation = service.link_memories(
        owner_a,
        source.item.memory_id,
        target.item.memory_id,
        "supports",
    )
    replay = service.link_memories(
        owner_a,
        source.item.memory_id,
        target.item.memory_id,
        "supports",
    )

    assert replay == relation
    assert relation.origin is RelationOrigin.MANUAL
    assert relation.scope is RelationScope.ITEM
    assert relation.source_revision_id == source.current_revision.revision_id
    assert relation.target_revision_id == target.current_revision.revision_id
    assert relation.provenance is None
    outgoing = service.list_memory_relations(owner_a, source.item.memory_id)
    incoming = service.list_memory_relations(owner_a, target.item.memory_id)
    assert outgoing[0].direction is RelationDirection.OUTGOING
    assert outgoing[0].related_memory_id == target.item.memory_id
    assert incoming[0].direction is RelationDirection.INCOMING
    assert incoming[0].related_memory_id == source.item.memory_id

    with pytest.raises(MemoryNotFoundError, match="unavailable"):
        service.link_memories(
            owner_a,
            source.item.memory_id,
            foreign.item.memory_id,
            "supports",
        )
    with pytest.raises(MemoryNotFoundError, match="unavailable"):
        service.list_memory_relations(owner_b, source.item.memory_id)

    revoked = service.revoke_memory_relation(owner_a, relation.relation_id)
    assert revoked.status is RelationStatus.REVOKED
    assert service.revoke_memory_relation(owner_a, relation.relation_id) == revoked
    assert service.list_memory_relations(owner_a, source.item.memory_id) == ()
    history = service.list_memory_relations(
        owner_a,
        source.item.memory_id,
        include_inactive=True,
    )
    assert history[0].relation == revoked
    with pytest.raises(MemoryRelationNotFoundError, match="unavailable"):
        service.revoke_memory_relation(owner_b, relation.relation_id)


def test_relation_rejects_self_direction_cross_profile_and_inactive_endpoint() -> None:
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [_relation_profile(), AlternateMemoryProfile()],
    )
    principal = PrincipalContext("analyst-a")
    source = service.create_memory(principal, project_preference_command())
    target = service.create_memory(principal, _ongoing_command())
    other_profile = service.create_memory(
        principal,
        replace(
            project_preference_command(),
            profile_id="personal-notes",
            subject="note",
            memory_type="note",
        ),
    )

    with pytest.raises(InvalidMemoryRelationError, match="self loop"):
        service.link_memories(
            principal,
            source.item.memory_id,
            source.item.memory_id,
            "supports",
        )
    with pytest.raises(InvalidMemoryRelationError, match="endpoints"):
        service.link_memories(
            principal,
            target.item.memory_id,
            source.item.memory_id,
            "supports",
        )
    with pytest.raises(InvalidMemoryRelationError, match="share a profile"):
        service.link_memories(
            principal,
            source.item.memory_id,
            other_profile.item.memory_id,
            "supports",
        )

    service.revoke_memory(principal, target.item.memory_id)
    with pytest.raises(InvalidMemoryRelationError, match="could not be created"):
        service.link_memories(
            principal,
            source.item.memory_id,
            target.item.memory_id,
            "supports",
        )


def test_relation_rejects_an_expired_endpoint() -> None:
    observed_at = datetime(2026, 7, 29, 10, tzinfo=UTC)
    current_time = [observed_at]
    profile = replace(
        _relation_profile(),
        metadata_policies={
            "preference": MemoryMetadataPolicy(),
            "ongoing_item": MemoryMetadataPolicy(validity_days=1),
            "stable_context": MemoryMetadataPolicy(),
        },
    )
    repository = InMemoryMemoryRepository()
    service = MemoryService(
        repository,
        ProfileRegistry(),
        sensitive_guard=RegexSensitiveContentGuard(),
        clock=lambda: current_time[0],
    )
    service.register_profile(profile)
    principal = PrincipalContext("analyst-a")
    source = service.create_memory(principal, project_preference_command())
    target = service.create_memory(principal, _ongoing_command())
    relation = service.link_memories(
        principal,
        source.item.memory_id,
        target.item.memory_id,
        "supports",
    )
    current_time[0] = observed_at + timedelta(days=1)

    result = service.run_maintenance()

    with pytest.raises(InvalidMemoryRelationError, match="could not be created"):
        service.link_memories(
            principal,
            source.item.memory_id,
            target.item.memory_id,
            "supports",
        )
    assert result.expired_memory_count == 1
    assert result.stale_relation_count == 1
    assert (
        service.get_memory(
            principal,
            target.item.memory_id,
        ).current_revision.lifecycle_status
        is LifecycleStatus.EXPIRED
    )
    assert service.list_memory_relations(principal, source.item.memory_id) == ()
    history = service.list_memory_relations(
        principal,
        source.item.memory_id,
        include_inactive=True,
    )
    assert history[0].relation.relation_id == relation.relation_id
    assert history[0].relation.status is RelationStatus.STALE
    assert history[0].relation.stale_reason == "endpoint_expired"

    replay = service.run_maintenance()
    assert replay.expired_memory_count == 0
    assert replay.stale_relation_count == 0


def test_relation_survives_endpoint_replacement_and_domain_invariants() -> None:
    expression = "以后项目周报改为 Markdown"
    extractor = FakeCandidateExtractor(
        (
            candidate_proposal(
                expression,
                content="项目周报默认使用 Markdown",
            ),
        )
    )
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [_relation_profile()],
        candidate_extractor=extractor,
    )
    principal = PrincipalContext("analyst-a")
    source = service.create_memory(principal, project_preference_command())
    target = service.create_memory(principal, _ongoing_command())
    relation = service.link_memories(
        principal,
        source.item.memory_id,
        target.item.memory_id,
        "supports",
    )

    from memory_mcp.core import MessageRole, TurnEnvelope, TurnMessage

    service.capture_turn(
        principal,
        TurnEnvelope(
            profile_id="project-work",
            conversation_id="replacement-session",
            source_turn_id="replacement-turn",
            content=expression,
            observed_at=datetime(2026, 7, 30, 10, tzinfo=UTC),
            subject_hint="weekly-report",
            messages=(
                TurnMessage(
                    role=MessageRole.USER,
                    content=expression,
                    message_id="replacement-message",
                ),
            ),
        ),
    )

    replaced = service.get_memory(principal, source.item.memory_id)
    assert replaced.current_revision.revision_number == 2
    assert (
        service.list_memory_relations(principal, source.item.memory_id)[0].relation
        == relation
    )
    with pytest.raises(ValueError, match="revoked relation requires"):
        replace(relation, status=RelationStatus.REVOKED)
    with pytest.raises(ValueError, match="self loop"):
        replace(relation, target_memory_id=relation.source_memory_id)
    with pytest.raises(ValueError, match="must not precede"):
        replace(
            relation,
            status=RelationStatus.REVOKED,
            revoked_at=relation.created_at - timedelta(seconds=1),
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("owner_id", " "),
        ("observed_at", datetime(2026, 7, 29, 10)),
    ],
)
def test_invalid_identity_and_naive_time_are_rejected(
    field_name: str,
    value: object,
) -> None:
    if field_name == "owner_id":
        with pytest.raises(ValueError, match="owner_id"):
            PrincipalContext(value)  # type: ignore[arg-type]
        return

    command = project_preference_command()
    object.__setattr__(command, field_name, value)
    with pytest.raises(ValueError, match="timezone-aware"):
        command.__post_init__()


def test_created_timestamps_are_timezone_aware() -> None:
    fixed_now = datetime(2026, 7, 29, 12, tzinfo=UTC)
    repository = InMemoryMemoryRepository()
    service = MemoryService(
        repository,
        ProfileRegistry(),
        sensitive_guard=RegexSensitiveContentGuard(),
        clock=lambda: fixed_now,
    )
    service.register_profile(TestMemoryProfile())

    record = service.create_memory(
        PrincipalContext("analyst-a"),
        project_preference_command(),
    )

    assert record.item.created_at == fixed_now
    assert record.current_revision.created_at == fixed_now
    assert record.evidence[0].created_at == fixed_now


def test_revoke_stales_revision_scoped_automatic_relations() -> None:
    """revoke 端点时，指向它的 automatic/revision-scoped 活动关系应物化为 stale。

    item-scoped 手动关系不受影响。该不变量与 replacement 同源（见
    test_relation_survives_endpoint_replacement_and_domain_invariants）。
    """

    from uuid import uuid4

    from memory_mcp.core import ExpressionBasis, MemoryRelation, RelationProvenance

    service = create_memory_service(
        InMemoryMemoryRepository(),
        [_relation_profile()],
    )
    principal = PrincipalContext("analyst-a")
    source = service.create_memory(principal, project_preference_command())
    target = service.create_memory(principal, _ongoing_command())
    # 注入一条 automatic/revision-scoped 关系（正常经 capture 产出）
    automatic = MemoryRelation(
        relation_id=uuid4(),
        owner_id=principal.owner_id,
        profile_id=source.item.profile_id,
        source_memory_id=source.item.memory_id,
        target_memory_id=target.item.memory_id,
        relation_type="supports",
        status=RelationStatus.ACTIVE,
        created_at=source.current_revision.created_at,
        origin=RelationOrigin.AUTOMATIC,
        scope=RelationScope.REVISION,
        source_revision_id=source.current_revision.revision_id,
        target_revision_id=target.current_revision.revision_id,
        provenance=RelationProvenance(
            capture_id=uuid4(),
            conversation_id="test",
            source_turn_id="test-turn",
            source_expression="test",
            confidence=0.95,
            expression_basis=ExpressionBasis.EXPLICIT,
            model_id="test",
            prompt_version="test",
            schema_version="test",
        ),
    )
    repository = service._repository  # type: ignore[attr-defined]
    repository.register_profile(_relation_profile())
    repository._relations[automatic.relation_id] = automatic  # type: ignore[attr-defined]

    service.revoke_memory(principal, target.item.memory_id)

    relations = service.list_memory_relations(
        principal,
        target.item.memory_id,
        include_inactive=True,
    )
    assert relations, "relation should still exist after revoke"
    assert relations[0].relation.status is RelationStatus.STALE
    assert relations[0].relation.stale_reason == "endpoint_revoked"


def test_revoke_does_not_stale_item_scoped_manual_relations() -> None:
    """revoke 端点时，item-scoped 手动关系保持 active（与 replacement 一致）。"""

    service = create_memory_service(
        InMemoryMemoryRepository(),
        [_relation_profile()],
    )
    principal = PrincipalContext("analyst-a")
    source = service.create_memory(principal, project_preference_command())
    target = service.create_memory(principal, _ongoing_command())
    manual = service.link_memories(
        principal,
        source.item.memory_id,
        target.item.memory_id,
        "supports",
    )

    service.revoke_memory(principal, target.item.memory_id)

    relations = service.list_memory_relations(
        principal,
        target.item.memory_id,
        include_inactive=True,
    )
    assert any(
        r.relation.relation_id == manual.relation_id
        and r.relation.status is RelationStatus.ACTIVE
        for r in relations
    ), "manual/item-scoped relation must survive revoke"
