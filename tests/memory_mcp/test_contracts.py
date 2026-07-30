import json
from pathlib import Path
from uuid import uuid4

import anyio
import pytest
from memory.fakes import FakeCandidateExtractor, TestScenarioPolicy
from pydantic import SecretStr, ValidationError

from agent_lab.memory.adapters import InMemoryMemoryRepository
from agent_lab.memory.composition import create_memory_service
from agent_lab.memory_mcp import MemoryServerSettings, create_memory_mcp_server
from agent_lab.memory_mcp.schemas import CompletedTurnEventV1


def _settings(database_path: Path) -> MemoryServerSettings:
    return MemoryServerSettings(
        database_path=database_path,
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


def test_server_exposes_only_stage_three_tools_without_owner_inputs() -> None:
    path = Path(".agent-lab/test-memory") / f"contract-{uuid4().hex}.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        service = create_memory_service(
            InMemoryMemoryRepository(),
            [TestScenarioPolicy()],
            candidate_extractor=FakeCandidateExtractor(),
        )
        server = create_memory_mcp_server(
            _settings(path),
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
        }
        capture = next(tool for tool in tools if tool.name == "capture_completed_turn")
        serialized_schema = json.dumps(capture.inputSchema)
        assert "owner_id" not in serialized_schema
        assert "owner_key" not in serialized_schema
        assert "tenant_id" not in serialized_schema
        assert capture.inputSchema.get("additionalProperties") is False
    finally:
        path.unlink(missing_ok=True)


def test_server_settings_hide_tokens_and_fail_closed_without_mapping() -> None:
    settings = _settings(Path(".agent-lab/test-memory/settings.db"))

    assert "token-a" not in repr(settings)
    assert settings.require_demo_principals()["token-a"].owner_key == "analyst-a"

    empty = MemoryServerSettings()
    with pytest.raises(ValueError, match="At least one"):
        empty.require_demo_principals()
