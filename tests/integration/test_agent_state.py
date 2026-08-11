import json
from datetime import UTC, datetime, timedelta


def test_turn_state_cleanup_removes_expired_and_invalid_files(tmp_path) -> None:
    """cleanup_expired 清除过期与损坏的残留状态文件（Phase 1 后唯一保留的 API）。"""

    now = datetime(2026, 7, 31, 12, tzinfo=UTC)
    root = tmp_path / "hooks"
    root.mkdir(parents=True)
    # 写残留旧版本状态文件，模拟 Phase 1 前遗留的 outbox。
    (root / "old.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "session_id": "session-old",
                "turn_id": "turn-old",
                "prompt": "过期问题",
                "created_at": (now - timedelta(hours=2)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    (root / "current.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "session_id": "session-current",
                "turn_id": "turn-current",
                "prompt": "当前问题",
                "created_at": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    invalid = root / "invalid.json"
    invalid.write_text("{invalid", encoding="utf-8")

    from memory_mcp_agent.state import TurnStateStore

    store = TurnStateStore(
        root,
        ttl=timedelta(hours=1),
        now=lambda: now,
    )

    assert store.cleanup_expired() == 2
    assert not (root / "old.json").exists()
    assert (root / "current.json").exists()
    assert not invalid.exists()


def test_cleanup_expired_handles_missing_directory(tmp_path) -> None:
    """目标目录不存在时 cleanup_expired 安全返回 0。"""

    from memory_mcp_agent.state import TurnStateStore

    store = TurnStateStore(tmp_path / "absent")
    assert store.cleanup_expired() == 0
