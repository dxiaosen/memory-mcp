import stat
from datetime import UTC, datetime, timedelta

import pytest
from memory_mcp_agent.state import (
    TurnState,
    TurnStateConflictError,
    TurnStateStore,
)


def test_turn_state_is_atomic_restricted_and_uses_digest_path(tmp_path) -> None:
    root = tmp_path / "runtime" / "hooks"
    store = TurnStateStore(root)
    state = TurnState(
        session_id="../../session",
        turn_id="../turn/../../secret",
        prompt="以后默认使用中文",
    )

    store.save(state)

    files = tuple(root.glob("*.json"))
    assert len(files) == 1
    assert files[0].parent == root
    assert "/" not in files[0].name
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert store.load(state.session_id, state.turn_id) == state
    assert not tuple(root.glob("*.tmp"))


def test_turn_state_keeps_concurrent_turns_separate(tmp_path) -> None:
    store = TurnStateStore(tmp_path / "hooks")
    first = TurnState(
        session_id="session-1",
        turn_id="turn-1",
        prompt="第一个问题",
    )
    second = TurnState(
        session_id="session-1",
        turn_id="turn-2",
        prompt="第二个问题",
    )

    store.save(first)
    store.save(second)

    assert store.load("session-1", "turn-1") == first
    assert store.load("session-1", "turn-2") == second
    store.delete("session-1", "turn-1")
    assert store.load("session-1", "turn-1") is None
    assert store.load("session-1", "turn-2") == second


def test_staged_capture_is_stable_and_legacy_prompt_state_still_loads(
    tmp_path,
) -> None:
    observed_at = datetime(2026, 8, 2, 8, tzinfo=UTC)
    store = TurnStateStore(tmp_path / "hooks")
    store.save(
        TurnState(
            session_id="session-1",
            turn_id="turn-1",
            prompt="原始问题",
        )
    )

    staged = store.stage_capture(
        "session-1",
        "turn-1",
        final_output="最终回复",
        observed_at=observed_at,
        profile_id="research",
    )
    replayed = store.stage_capture(
        "session-1",
        "turn-1",
        final_output="最终回复",
        observed_at=observed_at + timedelta(minutes=1),
        profile_id="research",
    )

    assert replayed == staged
    assert replayed.schema_version == "1"
    assert replayed.capture_observed_at == observed_at

    legacy = TurnState.model_validate(
        {
            "schema_version": "1",
            "session_id": "legacy-session",
            "turn_id": "legacy-turn",
            "prompt": "旧版本状态",
            "created_at": observed_at,
        }
    )
    store.save(legacy)
    assert store.load("legacy-session", "legacy-turn") == legacy


def test_turn_state_rejects_conflicting_prompt_for_same_turn(tmp_path) -> None:
    store = TurnStateStore(tmp_path / "hooks")
    store.save(
        TurnState(
            session_id="session-1",
            turn_id="turn-1",
            prompt="原始问题",
        )
    )

    with pytest.raises(
        TurnStateConflictError,
        match="turn_state_payload_conflict",
    ):
        store.save(
            TurnState(
                session_id="session-1",
                turn_id="turn-1",
                prompt="不同问题",
            )
        )


def test_turn_state_cleanup_removes_expired_and_invalid_files(tmp_path) -> None:
    now = datetime(2026, 7, 31, 12, tzinfo=UTC)
    root = tmp_path / "hooks"
    store = TurnStateStore(
        root,
        ttl=timedelta(hours=1),
        now=lambda: now,
    )
    store.save(
        TurnState(
            session_id="session-old",
            turn_id="turn-old",
            prompt="过期问题",
            created_at=now - timedelta(hours=2),
        )
    )
    current = TurnState(
        session_id="session-current",
        turn_id="turn-current",
        prompt="当前问题",
        created_at=now,
    )
    store.save(current)
    invalid = root / "invalid.json"
    invalid.write_text("{invalid", encoding="utf-8")

    assert store.cleanup_expired() == 2
    assert store.load("session-old", "turn-old") is None
    assert store.load("session-current", "turn-current") == current
    assert not invalid.exists()
