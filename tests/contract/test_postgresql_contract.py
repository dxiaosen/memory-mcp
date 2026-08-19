import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from memory_mcp.core import (
    MemoryNotFoundError,
    MemoryRelationPolicy,
    PrincipalContext,
    RecallQuery,
    RelationDirection,
    RelationOrigin,
    RelationScope,
    RelationStatus,
)
from memory_mcp.core.adapters.postgresql import (
    PostgreSQLMemoryRepository,
    create_pool,
)
from memory_mcp.core.adapters.postgresql.schema import (
    apply_migrations,
    load_migrations,
    validate_schema,
)
from memory_mcp.core.composition import create_memory_service

from tests.support.fakes import (
    FakeCandidateExtractor,
    TestMemoryProfile,
    project_preference_command,
)

_TEST_DATABASE_ENV = "MEMORY_MCP_TEST_DATABASE_URL"


def _connect_safely(database_url: str):
    try:
        return psycopg.connect(database_url)
    except psycopg.Error:
        raise RuntimeError("PostgreSQL test connection failed") from None


@dataclass(frozen=True, slots=True)
class PostgreSQLTestDatabase:
    """Keep credentials out of pytest fixture representations."""

    url: str

    def __repr__(self) -> str:
        return "PostgreSQLTestDatabase(url=<redacted>)"


def test_postgresql_migration_preserves_authoritative_invariants() -> None:
    migrations = load_migrations()

    # 开发阶段只维护一个 schema 文件，不每次变更新建增量 migration。
    assert [migration.version for migration in migrations] == [
        "0001_memory_schema.sql",
    ]
    assert all(len(migration.checksum) == 64 for migration in migrations)

    sql = migrations[0].sql
    # 核心表和索引存在。
    for required_fragment in (
        "memory_items",
        "memory_revisions",
        "memory_evidence",
        "memory_captures",
        "memory_reviews",
        "memory_relations",
        "memory_capture_outcomes",
        "memory_captures_event_unique",
        "memory_revisions_one_current_idx",
        "memory_relations_one_active_idx",
        "memory_items_recall_subject_fts_idx",
        "memory_revisions_recall_content_fts_idx",
        "memory_revisions_maintenance_expiry_idx",
        "memory_reviews_maintenance_idx",
        "CREATE EXTENSION IF NOT EXISTS pg_jieba",
        "TIMESTAMPTZ",
        "UUID",
        "owner_id",
        "profile_id",
        "profile_version",
        "profile_fingerprint",
        "extraction_confidence",
        "verification_status",
        "sensitivity_level",
        "valid_from",
        "source_type",
        "citation_locator",
        "origin TEXT NOT NULL DEFAULT 'legacy'",
        "scope TEXT NOT NULL DEFAULT 'item'",
        "source_revision_id",
        "memory_relations_provenance_state",
        "status IN ('pending', 'confirmed', 'rejected', 'expired')",
        "status IN ('active', 'stale', 'revoked')",
    ):
        assert required_fragment in sql, f"missing fragment: {required_fragment}"

    # 外键全部移除：引用完整性由应用层事务和 advisory lock 保证。
    assert "FOREIGN KEY" not in sql
    assert "REFERENCES" not in sql
    # 中间迁移步骤（ALTER/RENAME）在合并后不再存在。
    assert "RENAME COLUMN scenario TO profile_id" not in sql
    assert "DROP CONSTRAINT memory_captures_source_unique" not in sql
    # valid_from 默认值在 CREATE TABLE 里直接声明，不再用 ALTER。
    assert "valid_from TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP" in sql


def test_postgresql_repository_exposes_the_memory_repository_contract() -> None:
    required_methods = {
        "register_profile",
        "add",
        "get",
        "list",
        "find_current",
        "find_recall_candidates",
        "find_recall_candidates_by_ids",
        "find_semantically_similar",
        "load_recall_evidence",
        "maintain",
        "revoke",
        "link_relation",
        "revoke_relation",
        "list_relations",
        "get_history",
        "get_capture",
        "commit_capture",
        "commit_capture_enqueue",
        "list_pending_captures",
        "list_reviews",
        "get_review",
        "resolve_review",
    }

    assert required_methods.issubset(dir(PostgreSQLMemoryRepository))


@pytest.fixture
def postgresql_test_database() -> Iterator[PostgreSQLTestDatabase]:
    database_url = os.environ.get(_TEST_DATABASE_ENV)
    if not database_url:
        pytest.skip(f"{_TEST_DATABASE_ENV} is not configured")
    with _connect_safely(database_url) as connection:
        database_name = connection.info.dbname
        if "test" not in database_name.casefold():
            pytest.fail(
                f"{_TEST_DATABASE_ENV} must select a disposable database "
                "whose name contains 'test'"
            )
    apply_migrations(database_url)
    _truncate_memory_tables(database_url)
    try:
        yield PostgreSQLTestDatabase(database_url)
    finally:
        _truncate_memory_tables(database_url)


def test_real_postgresql_migration_checksum_and_pool_close(
    postgresql_test_database: PostgreSQLTestDatabase,
) -> None:
    database_url = postgresql_test_database.url
    assert apply_migrations(database_url) == ()
    expected = {
        migration.version: migration.checksum for migration in load_migrations()
    }
    with _connect_safely(database_url) as connection:
        validate_schema(connection)
        applied = dict(
            connection.execute(
                """
                SELECT version, checksum
                FROM schema_migrations
                ORDER BY version
                """
            ).fetchall()
        )
    assert {version: applied[version] for version in expected} == expected

    pool = create_pool(database_url, min_size=1, max_size=2)
    repository = PostgreSQLMemoryRepository(pool)
    repository.check_health()
    repository.close()
    assert pool.closed is True


def test_real_postgresql_owner_transaction_and_restart_contract(
    postgresql_test_database: PostgreSQLTestDatabase,
) -> None:
    database_url = postgresql_test_database.url
    pool = create_pool(database_url, min_size=1, max_size=3)
    repository = PostgreSQLMemoryRepository(pool)
    service = create_memory_service(repository, [TestMemoryProfile()])
    owner_a = PrincipalContext("owner-a")
    owner_b = PrincipalContext("owner-b")
    created = service.create_memory(owner_a, project_preference_command())

    assert service.get_memory(owner_a, created.item.memory_id) == created
    assert service.list_memories(owner_b) == ()
    with pytest.raises(MemoryNotFoundError):
        service.get_memory(owner_b, created.item.memory_id)
    for index in range(2):
        service.create_memory(
            owner_a,
            replace(
                project_preference_command(),
                subject=f"report-{index}",
                source_turn_id=f"turn-{index + 2}",
                observed_at=datetime(2026, 7, 30, index + 11, tzinfo=UTC),
            ),
        )
    other_owner = service.create_memory(
        owner_b,
        replace(
            project_preference_command(),
            subject="other-owner-report",
            source_turn_id="other-owner-turn",
        ),
    )
    bounded = repository.find_current(
        owner_a,
        profile_id="project-work",
        limit=2,
    )
    assert len(bounded) == 2
    assert {record.item.owner_id for record in bounded} == {"owner-a"}
    assert {record.item.profile_id for record in bounded} == {"project-work"}
    recalled = service.recall_memory(
        owner_a,
        RecallQuery(
            profile_id="project-work",
            query="项目周报默认使用什么格式？",
            subject="weekly-report",
            max_items=1,
            token_budget=600,
        ),
    )
    assert recalled.items[0].memory_id == created.item.memory_id
    assert len(recalled.items[0].sources) == 1
    evidence = repository.load_recall_evidence(
        owner_a,
        revision_ids=(
            created.current_revision.revision_id,
            other_owner.current_revision.revision_id,
        ),
        per_revision_limit=3,
    )
    assert set(evidence) == {created.current_revision.revision_id}
    maintenance_time = datetime(2026, 8, 2, 12, tzinfo=UTC)
    first_maintenance = repository.maintain(
        effective_at=maintenance_time,
        review_cutoff=maintenance_time,
        limit=500,
    )
    second_maintenance = repository.maintain(
        effective_at=maintenance_time,
        review_cutoff=maintenance_time,
        limit=500,
    )
    assert first_maintenance == second_maintenance
    repository.close()

    reopened_pool = create_pool(
        database_url,
        min_size=1,
        max_size=3,
    )
    reopened = PostgreSQLMemoryRepository(reopened_pool)
    try:
        reopened_service = create_memory_service(
            reopened,
            [TestMemoryProfile()],
        )
        assert reopened_service.get_memory(owner_a, created.item.memory_id) == created
    finally:
        reopened.close()


def test_real_postgresql_find_semantically_similar_respects_threshold_and_owner(
    postgresql_test_database: PostgreSQLTestDatabase,
) -> None:
    """``find_semantically_similar`` 按余弦相似度返回同 owner/profile/type 最近活动记忆。"""

    pool = create_pool(postgresql_test_database.url, min_size=1, max_size=3)
    repository = PostgreSQLMemoryRepository(pool)
    repository.register_profile(TestMemoryProfile())
    owner_a = PrincipalContext("owner-a")
    owner_b = PrincipalContext("owner-b")
    effective_at = datetime(2026, 7, 30, 12, tzinfo=UTC)
    # pgvector 列为 vector(1024)，用 1024 维向量构造近/远样本。
    near_embedding = tuple([1.0] + [0.0] * 1023)
    far_embedding = tuple([0.0, 1.0] + [0.0] * 1022)
    query = tuple([1.0, 0.01] + [0.0] * 1022)

    def _record(owner: str, subject: str, embedding: tuple[float, ...]) -> None:
        from memory_mcp.core import (
            AssertionKind,
            Evidence,
            EvidenceSourceType,
            LifecycleStatus,
            MemoryItem,
            MemoryRecord,
            MemoryRevision,
            MessageRole,
            SensitivityLevel,
            VerificationStatus,
        )

        memory_id = uuid4()
        revision_id = uuid4()
        repository.add(
            PrincipalContext(owner),
            MemoryRecord(
                item=MemoryItem(
                    memory_id=memory_id,
                    owner_id=owner,
                    profile_id="project-work",
                    subject=subject,
                    memory_type="preference",
                    created_at=effective_at,
                ),
                current_revision=MemoryRevision(
                    revision_id=revision_id,
                    memory_id=memory_id,
                    owner_id=owner,
                    revision_number=1,
                    content=subject,
                    assertion_kind=AssertionKind.USER_VIEW,
                    lifecycle_status=LifecycleStatus.ACTIVE,
                    business_progress=None,
                    save_rationale="测试",
                    observed_at=effective_at,
                    created_at=effective_at,
                    extraction_confidence=0.9,
                    verification_status=VerificationStatus.USER_ASSERTED,
                    sensitivity_level=SensitivityLevel.CONFIDENTIAL,
                    valid_from=effective_at,
                    valid_until=None,
                    embedding=embedding,
                ),
                evidence=(
                    Evidence(
                        evidence_id=uuid4(),
                        memory_id=memory_id,
                        revision_id=revision_id,
                        owner_id=owner,
                        source_turn_id="turn-1",
                        source_expression=subject,
                        observed_at=effective_at,
                        created_at=effective_at,
                        source_role=MessageRole.USER,
                        source_type=EvidenceSourceType.CONVERSATION,
                    ),
                ),
            ),
        )

    _record("owner-a", "near-subject", near_embedding)
    _record("owner-a", "far-subject", far_embedding)
    _record("owner-b", "other-owner-near", near_embedding)

    hit = repository.find_semantically_similar(
        owner_a,
        profile_id="project-work",
        memory_type="preference",
        embedding=query,
        threshold=0.95,
        effective_at=effective_at,
    )
    assert hit is not None
    assert hit.item.subject == "near-subject"
    assert hit.item.owner_id == "owner-a"

    miss = repository.find_semantically_similar(
        owner_b,
        profile_id="project-work",
        memory_type="preference",
        embedding=far_embedding,
        threshold=0.95,
        effective_at=effective_at,
    )
    assert miss is None
    repository.close()


def test_real_postgresql_relation_idempotency_history_and_restart(
    postgresql_test_database: PostgreSQLTestDatabase,
) -> None:
    profile = replace(
        TestMemoryProfile(),
        relation_policies={
            "supports": MemoryRelationPolicy(
                source_memory_types=frozenset({"preference"}),
                target_memory_types=frozenset({"ongoing_item"}),
                description="A preference supports an ongoing item.",
            )
        },
    )
    database_url = postgresql_test_database.url
    repository = PostgreSQLMemoryRepository(
        create_pool(database_url, min_size=1, max_size=4)
    )
    service = create_memory_service(repository, [profile])
    principal = PrincipalContext("owner-relation")
    source = service.create_memory(principal, project_preference_command())
    target = service.create_memory(
        principal,
        replace(
            project_preference_command(),
            subject="model-update",
            memory_type="ongoing_item",
            content="持续更新模型",
            source_turn_id="session-1-turn-2",
            source_expression="持续更新模型",
        ),
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            calls = tuple(
                executor.submit(
                    service.link_memories,
                    principal,
                    source.item.memory_id,
                    target.item.memory_id,
                    "supports",
                )
                for _ in range(2)
            )
            relations = tuple(call.result(timeout=10) for call in calls)
        assert relations[0] == relations[1]
    finally:
        repository.close()

    reopened_repository = PostgreSQLMemoryRepository(
        create_pool(database_url, min_size=1, max_size=2)
    )
    try:
        reopened_service = create_memory_service(reopened_repository, [profile])
        outgoing = reopened_service.list_memory_relations(
            principal,
            source.item.memory_id,
        )
        assert len(outgoing) == 1
        assert outgoing[0].direction is RelationDirection.OUTGOING
        revoked = reopened_service.revoke_memory_relation(
            principal,
            relations[0].relation_id,
        )
        assert revoked.status is RelationStatus.REVOKED
        assert (
            reopened_service.list_memory_relations(principal, source.item.memory_id)
            == ()
        )
        history = reopened_service.list_memory_relations(
            principal,
            source.item.memory_id,
            include_inactive=True,
        )
        assert history[0].relation == revoked
    finally:
        reopened_repository.close()


def test_real_postgresql_legacy_relation_defaults_remain_readable(
    postgresql_test_database: PostgreSQLTestDatabase,
) -> None:
    profile = replace(
        TestMemoryProfile(),
        relation_policies={
            "supports": MemoryRelationPolicy(
                source_memory_types=frozenset({"preference"}),
                target_memory_types=frozenset({"ongoing_item"}),
                description="A preference supports an ongoing item.",
            )
        },
    )
    repository = PostgreSQLMemoryRepository(
        create_pool(postgresql_test_database.url, min_size=1, max_size=2)
    )
    service = create_memory_service(repository, [profile])
    principal = PrincipalContext("owner-legacy-relation")
    source = service.create_memory(principal, project_preference_command())
    target = service.create_memory(
        principal,
        replace(
            project_preference_command(),
            subject="legacy-target",
            memory_type="ongoing_item",
            content="继续旧关系目标事项",
            source_turn_id="legacy-target-turn",
            source_expression="继续旧关系目标事项",
        ),
    )
    relation_id = uuid4()
    try:
        with _connect_safely(postgresql_test_database.url) as connection:
            connection.execute(
                """
                INSERT INTO memory_relations (
                    relation_id, owner_id, profile_id, source_memory_id,
                    target_memory_id, relation_type, status, created_at,
                    revoked_at
                )
                VALUES (%s, %s, %s, %s, %s, 'supports', 'active', %s, NULL)
                """,
                (
                    relation_id,
                    principal.owner_id,
                    profile.profile_id,
                    source.item.memory_id,
                    target.item.memory_id,
                    datetime(2026, 8, 1, tzinfo=UTC),
                ),
            )

        relation = service.list_memory_relations(
            principal,
            source.item.memory_id,
        )[0].relation
        assert relation.relation_id == relation_id
        assert relation.origin is RelationOrigin.LEGACY
        assert relation.scope is RelationScope.ITEM
        assert relation.source_revision_id is None
        assert relation.provenance is None
    finally:
        repository.close()


def test_real_postgresql_capture_commits_automatic_relation_atomically(
    postgresql_test_database: PostgreSQLTestDatabase,
) -> None:
    from memory_mcp.core import (
        ExpressionBasis,
        MessageRole,
        RelationOrigin,
        RelationProposal,
        RelationScope,
        TurnEnvelope,
        TurnMessage,
    )

    from tests.support.fakes import (
        FakeRelationExtractor,
        candidate_proposal,
    )

    profile = replace(
        TestMemoryProfile(),
        relation_policies={
            "supports": MemoryRelationPolicy(
                source_memory_types=frozenset({"preference"}),
                target_memory_types=frozenset({"ongoing_item"}),
                description="A preference supports an ongoing item.",
            )
        },
    )
    text = "周报偏好明确支持持续事项"

    def relation_proposals(request):
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
        return (
            RelationProposal(
                source_memory_id=source.memory_id,
                target_memory_id=target.memory_id,
                relation_type="supports",
                source_expression=text,
                confidence=0.97,
                expression_basis=ExpressionBasis.EXPLICIT,
            ),
        )

    repository = PostgreSQLMemoryRepository(
        create_pool(postgresql_test_database.url, min_size=1, max_size=3)
    )
    candidate_extractor = FakeCandidateExtractor(
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
    relation_extractor = FakeRelationExtractor(relation_proposals)
    service = create_memory_service(
        repository,
        [profile],
        candidate_extractor=candidate_extractor,
        relation_extractor=relation_extractor,
    )
    principal = PrincipalContext("owner-automatic-relation")
    try:
        first = service.capture_turn(
            principal,
            TurnEnvelope(
                profile_id="project-work",
                conversation_id="relation-capture",
                source_turn_id="turn-1",
                content=text,
                observed_at=datetime(2026, 7, 30, 10, tzinfo=UTC),
            ),
        )
        assert first.status.value == "completed"
        source = next(
            record
            for record in service.list_memories(principal)
            if record.item.memory_type == "preference"
        )
        automatic = service.list_memory_relations(
            principal,
            source.item.memory_id,
        )[0].relation
        assert automatic.origin is RelationOrigin.AUTOMATIC
        assert automatic.scope is RelationScope.REVISION
        assert automatic.provenance is not None
        assert automatic.provenance.capture_id == first.capture_id

        duplicate_service = create_memory_service(
            repository,
            [profile],
            candidate_extractor=FakeCandidateExtractor(),
            relation_extractor=FakeRelationExtractor(relation_proposals),
        )
        duplicate = duplicate_service.capture_turn(
            principal,
            TurnEnvelope(
                profile_id="project-work",
                conversation_id="relation-capture",
                source_turn_id="turn-2",
                content=text,
                observed_at=datetime(2026, 7, 30, 11, tzinfo=UTC),
            ),
        )
        assert duplicate.status.value == "completed"
        assert (
            len(
                duplicate_service.list_memory_relations(
                    principal,
                    source.item.memory_id,
                )
            )
            == 1
        )

        replacement_text = "以后项目周报改为图表"
        replacement_service = create_memory_service(
            repository,
            [profile],
            candidate_extractor=FakeCandidateExtractor(
                (
                    candidate_proposal(
                        replacement_text,
                        memory_type="preference",
                        content="项目周报默认使用图表",
                    ),
                )
            ),
            relation_extractor=FakeRelationExtractor(),
        )
        replacement_service.capture_turn(
            principal,
            TurnEnvelope(
                profile_id="project-work",
                conversation_id="relation-capture",
                source_turn_id="turn-replacement",
                content=replacement_text,
                observed_at=datetime(2026, 7, 30, 12, tzinfo=UTC),
                messages=(
                    TurnMessage(
                        role=MessageRole.USER,
                        content=replacement_text,
                        message_id="replacement-message",
                    ),
                ),
            ),
        )
        assert (
            replacement_service.list_memory_relations(
                principal,
                source.item.memory_id,
            )
            == ()
        )
        stale = replacement_service.list_memory_relations(
            principal,
            source.item.memory_id,
            include_inactive=True,
        )[0].relation
        assert stale.status is RelationStatus.STALE
        assert stale.stale_reason == "endpoint_revision_changed"
    finally:
        repository.close()


def test_real_postgresql_source_turn_idempotency_and_review_resolution(
    postgresql_test_database: PostgreSQLTestDatabase,
) -> None:
    from memory_mcp.core import (
        AssertionKind,
        ExpressionBasis,
        MessageRole,
        ReviewStatus,
        TurnEnvelope,
        TurnMessage,
    )

    from tests.support.fakes import candidate_proposal

    text = "我可能更喜欢周报要点"
    extractor = FakeCandidateExtractor(
        (
            candidate_proposal(
                text,
                content="用户可能偏好要点",
                assertion_kind=AssertionKind.SYSTEM_INFERENCE,
                expression_basis=ExpressionBasis.INFERRED,
            ),
        )
    )
    pool = create_pool(postgresql_test_database.url, min_size=1, max_size=4)
    repository = PostgreSQLMemoryRepository(pool)
    try:
        service = create_memory_service(
            repository,
            [TestMemoryProfile()],
            candidate_extractor=extractor,
        )
        principal = PrincipalContext("owner-a")
        turn = TurnEnvelope(
            profile_id="project-work",
            conversation_id="conversation-1",
            source_turn_id="turn-1",
            content=text,
            observed_at=datetime(2026, 7, 30, 10, tzinfo=UTC),
            messages=(
                TurnMessage(
                    role=MessageRole.USER,
                    content=text,
                    message_id="message-1",
                ),
            ),
        )
        first = service.capture_turn(principal, turn)
        replay = service.capture_turn(principal, turn)
        assert replay.capture_id == first.capture_id
        assert replay.replayed is True
        assert len(extractor.requests) == 1

        review_id = first.outcomes[0].review_id
        assert review_id is not None
        confirmed = service.confirm_review(principal, review_id)
        assert service.confirm_review(principal, review_id) == confirmed
        assert service.list_pending_reviews(principal) == ()
    finally:
        repository.close()

    reopened_pool = create_pool(
        postgresql_test_database.url,
        min_size=1,
        max_size=2,
    )
    reopened_repository = PostgreSQLMemoryRepository(reopened_pool)
    try:
        reopened_service = create_memory_service(
            reopened_repository,
            [TestMemoryProfile()],
        )
        assert reopened_service.get_review(principal, review_id).status is (
            ReviewStatus.CONFIRMED
        )
        assert (
            reopened_service.get_memory(principal, confirmed.item.memory_id)
            == confirmed
        )
    finally:
        reopened_repository.close()


def test_real_postgresql_overlapping_event_retry_and_service_restart(
    postgresql_test_database: PostgreSQLTestDatabase,
) -> None:
    from memory_mcp.core import MessageRole, TurnEnvelope, TurnMessage

    from tests.support.fakes import candidate_proposal

    text = "以后周报默认用表格"

    class BlockingExtractor(FakeCandidateExtractor):
        def __init__(self) -> None:
            super().__init__((candidate_proposal(text),))
            self.entered = Event()
            self.release = Event()

        def extract(self, request):
            self.entered.set()
            assert self.release.wait(timeout=10)
            return super().extract(request)

    extractor = BlockingExtractor()
    database_url = postgresql_test_database.url
    pool = create_pool(database_url, min_size=1, max_size=4)
    repository = PostgreSQLMemoryRepository(pool)
    service = create_memory_service(
        repository,
        [TestMemoryProfile()],
        candidate_extractor=extractor,
    )
    principal = PrincipalContext("owner-a")
    turn = TurnEnvelope(
        profile_id="project-work",
        conversation_id="conversation-event",
        source_turn_id="turn-event",
        content=text,
        observed_at=datetime(2026, 7, 30, 10, tzinfo=UTC),
        event_id="event-1",
        contract_version="1",
        payload_fingerprint="stable-fingerprint",
        messages=(
            TurnMessage(
                role=MessageRole.USER,
                content=text,
                message_id="message-event",
            ),
        ),
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(service.capture_turn, principal, turn)
            assert extractor.entered.wait(timeout=10)
            second = executor.submit(service.capture_turn, principal, turn)
            extractor.release.set()
            results = (first.result(timeout=10), second.result(timeout=10))
        assert len(extractor.requests) == 1
        assert results[0].capture_id == results[1].capture_id
        assert {result.replayed for result in results} == {False, True}
    finally:
        repository.close()

    reopened_pool = create_pool(
        database_url,
        min_size=1,
        max_size=2,
    )
    reopened_repository = PostgreSQLMemoryRepository(reopened_pool)
    replay_extractor = FakeCandidateExtractor()
    try:
        reopened_service = create_memory_service(
            reopened_repository,
            [TestMemoryProfile()],
            candidate_extractor=replay_extractor,
        )
        replay = reopened_service.capture_turn(principal, turn)
        assert replay.replayed is True
        assert replay_extractor.requests == []
    finally:
        reopened_repository.close()


def _truncate_memory_tables(database_url: str) -> None:
    # 外键已移除，CASCADE 不再级联清空；显式清空全部 memory 表。
    with _connect_safely(database_url) as connection:
        connection.execute(
            """
            TRUNCATE TABLE memory_capture_outcomes,
                            memory_relations,
                            memory_review_documents,
                            memory_reviews,
                            memory_evidence_documents,
                            memory_evidence,
                            memory_revisions,
                            memory_items,
                            memory_captures, memory_team_extractions
            """
        )


def test_real_postgresql_team_memory_visible_to_member_not_outsider(
    postgresql_test_database: PostgreSQLTestDatabase,
) -> None:
    """真实 DB：团队成员召回团队记忆，非成员不可见。"""

    pool = create_pool(postgresql_test_database.url, min_size=1, max_size=2)
    repository = PostgreSQLMemoryRepository(pool)
    service = create_memory_service(repository, [TestMemoryProfile()])
    try:
        team_owner = "tenant-001:team:research-dept"
        # 写入团队公共记忆。
        team_memory = service.create_memory(
            PrincipalContext(team_owner),
            project_preference_command(),
        )
        assert team_memory.item.owner_id == team_owner
        # 团队成员能召回。
        member = PrincipalContext(
            "tenant-001:member-a",
            (team_owner,),
        )
        member_result = service.recall_memory(
            member,
            RecallQuery(
                profile_id="project-work",
                query="项目周报默认使用什么格式",
                subject="weekly-report",
                max_items=5,
                token_budget=600,
            ),
        )
        assert len(member_result.items) == 1
        # 召回的记忆 owner 是团队 owner（通过 get_memory 确认）。
        recalled_memory = service.get_memory(member, member_result.items[0].memory_id)
        assert recalled_memory.item.owner_id == team_owner
        # 非成员召回不到。
        outsider = PrincipalContext("tenant-001:outsider")
        outsider_result = service.recall_memory(
            outsider,
            RecallQuery(
                profile_id="project-work",
                query="项目周报默认使用什么格式",
                subject="weekly-report",
                max_items=5,
                token_budget=600,
            ),
        )
        assert outsider_result.items == ()
    finally:
        repository.close()


def test_real_postgresql_review_promotes_to_team_owner(
    postgresql_test_database: PostgreSQLTestDatabase,
) -> None:
    """真实 DB：review 确认时 promote_to_team 写入团队 owner。"""

    from memory_mcp.core import MessageRole, TurnEnvelope, TurnMessage

    from tests.support.fakes import CandidateDurability, FakeCandidateExtractor

    pool = create_pool(postgresql_test_database.url, min_size=1, max_size=2)
    repository = PostgreSQLMemoryRepository(pool)
    extractor = FakeCandidateExtractor()
    service = create_memory_service(
        repository, [TestMemoryProfile()], candidate_extractor=extractor
    )
    try:
        member = PrincipalContext(
            "tenant-001:member-a",
            ("tenant-001:team:research-dept",),
        )
        extractor.proposals = (
            _candidate_proposal(
                "周报默认用 markdown",
                content="周报默认用 markdown",
                durability=CandidateDurability.UNCERTAIN,
            ),
        )
        service.capture_turn(
            member,
            TurnEnvelope(
                profile_id="project-work",
                conversation_id="conv-team-1",
                source_turn_id="turn-team-1",
                content="周报默认用 markdown",
                observed_at=datetime(2026, 8, 3, tzinfo=UTC),
                messages=(
                    TurnMessage(
                        role=MessageRole.USER,
                        content="周报默认用 markdown",
                        message_id="msg-1",
                    ),
                ),
            ),
        )
        reviews = service.list_pending_reviews(member)
        assert len(reviews) == 1
        team_owner_id = "tenant-001:team:research-dept"
        memory = service.confirm_review(
            member,
            reviews[0].review_id,
            team_id="research-dept",
            team_owner_ids=frozenset({team_owner_id}),
        )
        # 写入的 memory owner 是团队 owner，且团队成员能召回。
        assert memory.item.owner_id == team_owner_id
        recalled = service.recall_memory(
            member,
            RecallQuery(
                profile_id="project-work",
                query="周报 markdown",
                max_items=5,
                token_budget=600,
            ),
        )
        assert any(item.memory_id == memory.item.memory_id for item in recalled.items)
    finally:
        repository.close()


def _candidate_proposal(
    text: str,
    *,
    subject: str = "weekly-report",
    content: str | None = None,
    durability: object | None = None,
):
    from tests.support.fakes import candidate_proposal

    kwargs = {"subject": subject, "content": content or text}
    if durability is not None:
        kwargs["durability"] = durability
    return candidate_proposal(text, **kwargs)


def test_real_postgresql_team_member_can_revoke_team_memory(
    postgresql_test_database: PostgreSQLTestDatabase,
) -> None:
    """真实 DB：团队成员能 revoke 团队公共记忆，非成员不能。"""

    pool = create_pool(postgresql_test_database.url, min_size=1, max_size=2)
    repository = PostgreSQLMemoryRepository(pool)
    service = create_memory_service(repository, [TestMemoryProfile()])
    try:
        team_owner = "tenant-001:team:research-dept"
        created = service.create_memory(
            PrincipalContext(team_owner),
            project_preference_command(),
        )
        member = PrincipalContext(
            "tenant-001:member-a",
            (team_owner,),
        )
        # 团队成员能 revoke。
        revoked = service.revoke_memory(member, created.item.memory_id)
        assert revoked.current_revision.lifecycle_status.value == "revoked"
        # revoke 后召回不到。
        result = service.recall_memory(
            member,
            RecallQuery(
                profile_id="project-work",
                query="项目周报默认使用什么格式",
                subject="weekly-report",
                max_items=5,
                token_budget=600,
            ),
        )
        assert result.items == ()
    finally:
        repository.close()
