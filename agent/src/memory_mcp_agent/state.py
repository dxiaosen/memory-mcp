"""独立 Agent Hook 进程之间的短期轮次状态。

Phase 1（模型自主调用 capture）后，hook 不再写入 outbox，本地状态目录仅靠
24h TTL 清理残留的旧版本文件。本模块只保留 ``TurnStateStore`` 的构造、
``for_working_directory`` 与 ``cleanup_expired``，用于过渡期清理；capture
相关的 save/stage_capture/pending_captures 等已随 Stop hook capture 路径移除。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from memory_mcp_agent.logging import log_event

_LOGGER = logging.getLogger(__name__)

_DEFAULT_TTL = timedelta(hours=24)


class TurnStateError(ValueError):
    """本地轮次状态损坏或与请求不一致。"""


class _LegacyTurnState(BaseModel):
    """仅用于读取残留旧版本状态文件以判断过期；不再写入。"""

    model_config = ConfigDict(extra="ignore", frozen=True)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("created_at")
    @classmethod
    def require_aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class TurnStateStore:
    """本地状态目录的过渡期清理器。

    Phase 1 后 hook 不再写入 outbox，本类仅负责按 TTL 清除残留的旧版本状态
    文件，保证本地目录自然清空。
    """

    def __init__(
        self,
        root: Path,
        *,
        ttl: timedelta = _DEFAULT_TTL,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("ttl must be positive")
        self._root = root
        self._ttl = ttl
        self._now = now or (lambda: datetime.now(UTC))

    @classmethod
    def for_working_directory(cls, cwd: str | Path) -> TurnStateStore:
        return cls(Path(cwd) / ".memory-mcp" / "hooks")

    def cleanup_expired(self) -> int:
        """清除过期或损坏状态；损坏文件记日志后清理，不单独记录其正文。"""

        if not self._root.exists():
            return 0
        cutoff = self._now() - self._ttl
        removed = 0
        for path in self._root.glob("*.json"):
            try:
                state = _LegacyTurnState.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                expired = state.created_at < cutoff
            except (OSError, ValueError) as exc:
                # 损坏文件当过期处理删除，但必须留痕，否则历史残留静默堆积。
                log_event(
                    _LOGGER,
                    logging.WARNING,
                    "turn_state.cleanup_corrupt",
                    path=str(path),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                expired = True
            if expired:
                try:
                    path.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
        return removed
