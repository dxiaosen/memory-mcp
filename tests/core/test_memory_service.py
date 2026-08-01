from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from memory_mcp.core import (
    InvalidMemoryProfileError,
    InvalidMemoryTypeError,
    InvalidProfileProgressError,
    LifecycleStatus,
    MemoryMetadataPolicy,
    MemoryNotFoundError,
    MemoryService,
    PrincipalContext,
    ProfileNotRegisteredError,
    ProfileRegistry,
    RecallQuery,
    SensitiveContentBlockedError,
    SensitivityLevel,
    VerificationStatus,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.adapters.sensitive import RegexSensitiveContentGuard
from memory_mcp.core.composition import create_memory_service

from tests.support.fakes import TestMemoryProfile, project_preference_command


def _service():
    return create_memory_service(
        InMemoryMemoryRepository(),
        [TestMemoryProfile()],
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
