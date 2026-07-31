import json

import anyio
import pytest
from memory_mcp.app import _run_server, create_memory_mcp_server
from memory_mcp.auth import StaticTokenVerifier
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.composition import create_memory_service
from memory_mcp.schemas import CompletedTurnEventV1
from memory_mcp.settings import MemoryServerSettings
from pydantic import SecretStr, ValidationError

from tests.support.fakes import FakeCandidateExtractor, TestMemoryProfile

_TOKEN_A = "analyst-a-primary-token-000000000001"


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
    }
    capture = next(tool for tool in tools if tool.name == "capture_completed_turn")
    serialized_schema = json.dumps(capture.inputSchema)
    assert "owner_id" not in serialized_schema
    assert "owner_key" not in serialized_schema
    assert "tenant_id" not in serialized_schema
    assert capture.inputSchema.get("additionalProperties") is False
    assert "profile_id" not in capture.inputSchema["required"]
    assert capture.inputSchema["properties"]["profile_id"]["default"] == "general-work"
    recall = next(tool for tool in tools if tool.name == "recall_memory")
    assert "profile_id" not in recall.inputSchema["required"]
    assert recall.inputSchema["properties"]["profile_id"]["default"] == "general-work"


def test_server_settings_hide_tokens_and_fail_closed_without_mapping() -> None:
    settings = _settings()

    assert _TOKEN_A not in repr(settings)
    assert settings.log_content is False
    assert (
        settings.require_configured_principals()[_TOKEN_A].owner_key
        == "default:analyst-a"
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
    assert first.claims == {"tenant_id": "default"}


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


def test_blank_log_file_selects_console_only_logging() -> None:
    settings = MemoryServerSettings(log_file="", _env_file=None)

    assert settings.log_file is None


def test_database_pool_bounds_are_consistent() -> None:
    with pytest.raises(ValidationError, match="pool_max_size"):
        MemoryServerSettings(
            database_pool_min_size=5,
            database_pool_max_size=4,
            _env_file=None,
        )


def test_interactive_shutdown_does_not_expose_keyboard_interrupt() -> None:
    class InterruptingServer:
        def run(self, *, transport: str) -> None:
            assert transport == "streamable-http"
            raise KeyboardInterrupt

    _run_server(InterruptingServer())
