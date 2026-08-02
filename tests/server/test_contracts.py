import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import anyio
import pytest
from memory_mcp.app import (
    _run_maintenance_loop,
    _run_server,
    _validate_default_profiles,
    create_memory_mcp_server,
)
from memory_mcp.auth import StaticTokenVerifier
from memory_mcp.core import (
    ExpressionBasis,
    MaintenanceResult,
    MemoryRelation,
    RelationOrigin,
    RelationProvenance,
    RelationScope,
    RelationStatus,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.composition import create_memory_service
from memory_mcp.schemas import CompletedTurnEventV1, MemoryRelationView
from memory_mcp.settings import MemoryServerSettings
from pydantic import SecretStr, ValidationError

from tests.support.fakes import FakeCandidateExtractor, TestMemoryProfile

_TOKEN_A = "analyst-a-primary-token-000000000001"


def test_relation_dto_exposes_governance_and_can_hide_provenance() -> None:
    relation = MemoryRelation(
        relation_id=UUID("10000000-0000-0000-0000-000000000001"),
        owner_id="owner-a",
        profile_id="investment-research",
        source_memory_id=UUID("20000000-0000-0000-0000-000000000001"),
        target_memory_id=UUID("30000000-0000-0000-0000-000000000001"),
        relation_type="supports",
        status=RelationStatus.ACTIVE,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        origin=RelationOrigin.AUTOMATIC,
        scope=RelationScope.REVISION,
        source_revision_id=UUID("40000000-0000-0000-0000-000000000001"),
        target_revision_id=UUID("50000000-0000-0000-0000-000000000001"),
        provenance=RelationProvenance(
            capture_id=UUID("60000000-0000-0000-0000-000000000001"),
            conversation_id="conversation-1",
            source_turn_id="turn-1",
            source_expression="证据明确支持论点",
            confidence=0.96,
            expression_basis=ExpressionBasis.EXPLICIT,
            model_id="model-a",
            prompt_version="relation-prompt-v1",
            schema_version="relation-v1",
        ),
    )

    visible = MemoryRelationView.from_relation(relation)
    hidden = MemoryRelationView.from_relation(
        relation,
        include_provenance=False,
    )

    assert visible.origin == "automatic"
    assert visible.scope == "revision"
    assert visible.provenance is not None
    assert visible.provenance.source_expression == "证据明确支持论点"
    assert hidden.provenance is None

    legacy = replace(
        relation,
        origin=RelationOrigin.LEGACY,
        scope=RelationScope.ITEM,
        source_revision_id=None,
        target_revision_id=None,
        provenance=None,
    )
    assert legacy.origin is RelationOrigin.LEGACY
    with pytest.raises(ValueError, match="legacy relation must"):
        replace(
            legacy,
            source_revision_id=relation.source_revision_id,
            target_revision_id=relation.target_revision_id,
        )
    with pytest.raises(ValueError, match="automatic relation requires"):
        replace(legacy, origin=RelationOrigin.AUTOMATIC)
    with pytest.raises(ValueError, match="stale relation requires"):
        replace(relation, status=RelationStatus.STALE)


def _settings() -> MemoryServerSettings:
    return MemoryServerSettings(
        auth_tokens=SecretStr(
            json.dumps(
                {
                    _TOKEN_A: {
                        "tenant_id": "default",
                        "subject_id": "analyst-a",
                        "scopes": [
                            "memory:read",
                            "memory:write",
                            "memory:review",
                        ],
                    }
                }
            )
        ),
        _env_file=None,
    )


def test_completed_turn_is_strict_versioned_and_fingerprint_stable() -> None:
    payload = {
        "contract_version": "1",
        "event_id": "event-1",
        "profile_id": "project-work",
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "observed_at": "2026-07-30T10:00:00+08:00",
        "messages": [
            {
                "role": "user",
                "content": "以后项目周报默认用表格",
            },
            {
                "role": "assistant",
                "content": "好的。",
            },
            {
                "role": "tool",
                "content": "年报第 42 页披露收入数据。",
                "message_id": "tool-message-1",
                "tool_name": "document_reader",
                "source_type": "document",
                "source_uri": "https://research.example/annual-2025",
                "source_title": "示例公司 2025 年报",
                "source_publisher": "示例交易所",
                "published_at": "2026-03-20T09:00:00+08:00",
                "retrieved_at": "2026-07-30T09:00:00+08:00",
                "content_hash": "sha256:example-report",
                "citation_locator": "p.42",
            },
        ],
    }

    first = CompletedTurnEventV1.model_validate(payload)
    second = CompletedTurnEventV1.model_validate(payload)

    assert first.payload_fingerprint() == second.payload_fingerprint()
    turn = first.to_turn_envelope(max_characters=10_000)
    assert turn.event_id == "event-1"
    assert turn.contract_version == "1"
    assert "[user]\n以后项目周报默认用表格" in turn.content
    assert turn.observed_at.isoformat() == "2026-07-30T10:00:00+08:00"
    assert turn.messages[2].source_type == "document"
    assert turn.messages[2].source_uri == ("https://research.example/annual-2025")
    assert turn.messages[2].citation_locator == "p.42"

    with pytest.raises(ValidationError):
        CompletedTurnEventV1.model_validate({**payload, "owner_id": "other-user"})


def test_server_exposes_stage_four_tools_without_owner_inputs() -> None:
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [TestMemoryProfile()],
        candidate_extractor=FakeCandidateExtractor(),
    )
    server = create_memory_mcp_server(
        _settings(),
        memory_service=service,
    )

    tools = anyio.run(server.list_tools)

    assert {tool.name for tool in tools} == {
        "capture_completed_turn",
        "list_memories",
        "get_memory",
        "list_pending_reviews",
        "confirm_pending_memory",
        "reject_pending_memory",
        "recall_memory",
        "revoke_memory",
        "link_memories",
        "revoke_memory_relation",
    }
    capture = next(tool for tool in tools if tool.name == "capture_completed_turn")
    serialized_schema = json.dumps(capture.inputSchema)
    assert "owner_id" not in serialized_schema
    assert "owner_key" not in serialized_schema
    assert "tenant_id" not in serialized_schema
    assert capture.inputSchema.get("additionalProperties") is False
    assert "profile_id" not in capture.inputSchema["required"]
    assert capture.inputSchema["properties"]["profile_id"]["default"] is None
    recall = next(tool for tool in tools if tool.name == "recall_memory")
    assert "profile_id" not in recall.inputSchema["required"]
    assert recall.inputSchema["properties"]["profile_id"]["default"] is None
    for relation_tool_name in ("link_memories", "revoke_memory_relation"):
        relation_tool = next(tool for tool in tools if tool.name == relation_tool_name)
        relation_schema = json.dumps(relation_tool.inputSchema)
        assert "owner_id" not in relation_schema
        assert "owner_key" not in relation_schema
        assert "tenant_id" not in relation_schema
        assert relation_tool.inputSchema.get("additionalProperties") is False


def test_server_settings_hide_tokens_and_fail_closed_without_mapping() -> None:
    settings = _settings()

    assert _TOKEN_A not in repr(settings)
    assert settings.log_content is False
    assert (
        settings.require_configured_principals()[_TOKEN_A].owner_key
        == "default:analyst-a"
    )
    assert (
        settings.require_configured_principals()[_TOKEN_A].default_profile_id
        == "general-work"
    )

    empty = MemoryServerSettings(
        auth_tokens=SecretStr("{}"),
        _env_file=None,
    )
    with pytest.raises(ValueError, match="At least one"):
        empty.require_configured_principals()


def test_configured_principal_mapping_rejects_derived_identity_fields() -> None:
    settings = MemoryServerSettings(
        auth_tokens=SecretStr(
            json.dumps(
                {
                    _TOKEN_A: {
                        "owner_key": "shared-owner",
                        "tenant_id": "default",
                        "subject_id": "user-a",
                    },
                }
            )
        )
    )

    with pytest.raises(ValidationError, match="owner_key"):
        settings.require_configured_principals()


def test_static_verifier_derives_an_opaque_stable_client_id() -> None:
    mappings = _settings().require_configured_principals()
    verifier = StaticTokenVerifier(mappings)

    first = anyio.run(verifier.verify_token, _TOKEN_A)
    second = anyio.run(verifier.verify_token, _TOKEN_A)

    assert first is not None
    assert second is not None
    assert first.client_id == second.client_id
    assert first.client_id.startswith("static-")
    assert _TOKEN_A not in first.client_id
    assert first.claims == {
        "default_profile_id": "general-work",
        "tenant_id": "default",
    }


def test_configured_principal_validates_default_profile_identifier() -> None:
    settings = MemoryServerSettings(
        auth_tokens=SecretStr(
            json.dumps(
                {
                    _TOKEN_A: {
                        "subject_id": "analyst-a",
                        "default_profile_id": "investment research",
                    }
                }
            )
        ),
        _env_file=None,
    )

    with pytest.raises(ValidationError, match="default_profile_id"):
        settings.require_configured_principals()


def test_server_rejects_unregistered_principal_default_profile() -> None:
    principals = _settings().require_configured_principals().values()

    with pytest.raises(ValueError, match="default_profile_id is not registered"):
        _validate_default_profiles(principals, [TestMemoryProfile()])


def test_configured_principal_rejects_ambiguous_identity_components() -> None:
    settings = MemoryServerSettings(
        auth_tokens=SecretStr(
            json.dumps(
                {
                    _TOKEN_A: {
                        "tenant_id": "tenant:ambiguous",
                        "subject_id": "user-a",
                    },
                }
            )
        ),
        _env_file=None,
    )

    with pytest.raises(ValidationError, match="tenant_id"):
        settings.require_configured_principals()


def test_configured_principal_mapping_rejects_short_tokens() -> None:
    settings = MemoryServerSettings(
        auth_tokens=SecretStr(
            json.dumps(
                {
                    "short-token": {
                        "subject_id": "owner",
                    }
                }
            )
        ),
        _env_file=None,
    )

    with pytest.raises(ValueError, match="at least 32"):
        settings.require_configured_principals()


def test_postgresql_settings_require_and_hide_database_url() -> None:
    settings = MemoryServerSettings(
        database_url=SecretStr(
            "postgresql://memory_app:secret-password@db.internal/memory_mcp"
        ),
        _env_file=None,
    )

    assert "secret-password" not in repr(settings)
    assert settings.require_postgresql_url().startswith("postgresql://")

    missing = MemoryServerSettings(database_url=None, _env_file=None)
    with pytest.raises(ValueError, match="DATABASE_URL"):
        missing.require_postgresql_url()


def test_database_pool_bounds_are_consistent() -> None:
    with pytest.raises(ValidationError, match="pool_max_size"):
        MemoryServerSettings(
            database_pool_min_size=5,
            database_pool_max_size=4,
            _env_file=None,
        )


def test_maintenance_interval_can_be_disabled_but_not_negative() -> None:
    assert (
        MemoryServerSettings(
            maintenance_interval_seconds=0,
            _env_file=None,
        ).maintenance_interval_seconds
        == 0
    )
    with pytest.raises(ValidationError, match="maintenance_interval_seconds"):
        MemoryServerSettings(
            maintenance_interval_seconds=-1,
            _env_file=None,
        )


def test_maintenance_loop_drains_backlog_and_stops_cleanly() -> None:
    calls = 0

    def operation() -> MaintenanceResult:
        nonlocal calls
        calls += 1
        return MaintenanceResult(
            effective_at=datetime(2026, 8, 2, tzinfo=UTC),
            expired_memory_count=1 if calls == 1 else 0,
            expired_review_count=0,
            stale_relation_count=0,
            has_more=calls == 1,
        )

    async def scenario() -> None:
        stop_event = asyncio.Event()

        async def stop_after_drain() -> None:
            while calls < 2:
                await asyncio.sleep(0)
            stop_event.set()

        await asyncio.gather(
            _run_maintenance_loop(
                operation,
                interval_seconds=60,
                stop_event=stop_event,
            ),
            stop_after_drain(),
        )

    anyio.run(scenario)
    assert calls == 2


def test_interactive_shutdown_does_not_expose_keyboard_interrupt() -> None:
    class InterruptingServer:
        def run(self, *, transport: str) -> None:
            assert transport == "streamable-http"
            raise KeyboardInterrupt

    _run_server(InterruptingServer())
