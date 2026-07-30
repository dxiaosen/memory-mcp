import json

import anyio
import pytest
from pydantic import SecretStr, ValidationError

from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.composition import create_memory_service
from memory_mcp.server.app import create_memory_mcp_server
from memory_mcp.server.schemas import CompletedTurnEventV1
from memory_mcp.server.settings import MemoryServerSettings
from tests.support.fakes import FakeCandidateExtractor, TestScenarioPolicy


def _settings() -> MemoryServerSettings:
    return MemoryServerSettings(
        demo_tokens_json=SecretStr(
            json.dumps(
                {
                    "token-a": {
                        "owner_key": "analyst-a",
                        "tenant_id": "demo",
                        "subject_id": "analyst-a",
                        "client_id": "agent-a",
                        "scopes": [
                            "memory:read",
                            "memory:write",
                            "memory:review",
                        ],
                    }
                }
            )
        ),
    )


def test_completed_turn_is_strict_versioned_and_fingerprint_stable() -> None:
    payload = {
        "contract_version": "1",
        "event_id": "event-1",
        "scenario": "project-work",
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
        [TestScenarioPolicy()],
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


def test_server_settings_hide_tokens_and_fail_closed_without_mapping() -> None:
    settings = _settings()

    assert "token-a" not in repr(settings)
    assert settings.require_demo_principals()["token-a"].owner_key == "analyst-a"

    empty = MemoryServerSettings(
        demo_tokens_json=SecretStr("{}"),
        _env_file=None,
    )
    with pytest.raises(ValueError, match="At least one"):
        empty.require_demo_principals()


def test_demo_principal_mapping_rejects_owner_aliases() -> None:
    settings = MemoryServerSettings(
        demo_tokens_json=SecretStr(
            json.dumps(
                {
                    "token-a": {
                        "owner_key": "shared-owner",
                        "tenant_id": "demo",
                        "subject_id": "user-a",
                        "client_id": "agent",
                    },
                    "token-b": {
                        "owner_key": "shared-owner",
                        "tenant_id": "demo",
                        "subject_id": "user-b",
                        "client_id": "agent",
                    },
                }
            )
        )
    )

    with pytest.raises(ValueError, match="must not alias"):
        settings.require_demo_principals()


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
