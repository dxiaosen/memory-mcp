"""独立 Agent Hook 进程之间的短期轮次状态。

Stop hook 恢复强制捕获后，BeforeRun 存 user_input（prompt）、AfterRun 取出并入队
capture。本地状态只做单轮 prompt 暂存（BeforeRun→AfterRun 跨进程），不做 outbox
补投——入队失败走 fail-open，下一轮 Stop 用同 conversation_id+turn_id 再入队时服务端
event_id 幂等兜底。``TurnStateStore`` 负责原子写、精确读、TTL 清理。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from memory_mcp_agent.logging import log_event

_LOGGER = logging.getLogger(__name__)

_STATE_SCHEMA_VERSION = "1"
# 仍保留 "2" 在支持集合内，保证本地残留的旧版本状态文件在 24h TTL 内可被
# 平滑读出而不报错；新写入一律标 "1"。
_SUPPORTED_STATE_SCHEMA_VERSIONS = frozenset({_STATE_SCHEMA_VERSION, "2"})
_DEFAULT_TTL = timedelta(hours=24)


class TurnStateError(ValueError):
    """本地轮次状态损坏或与请求不一致。"""


class TurnStateConflictError(TurnStateError):
    """同一个宿主轮次标识被不同用户输入重用。"""


class TurnState(BaseModel):
    """单轮次的最小持久状态：BeforeRun 存 prompt，AfterRun 取出入队 capture。

    不再暂存 capture payload（final_output/observed_at/document_messages）——
    Stop 事件直接带 final_output，入队毫秒级返回，无需 outbox 重投。
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    schema_version: str = _STATE_SCHEMA_VERSION
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    profile_id: str | None = Field(default=None, min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("created_at")
    @classmethod
    def require_aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class TurnStateStore:
    """本地轮次状态目录：原子保存 prompt，BeforeRun→AfterRun 跨进程取出。

    用摘要文件名和原子替换保存短期轮次状态。BeforeRun 写入 prompt，AfterRun
    取出后删除。入队失败不再保留待重投——走 fail-open，下轮幂等兜底。
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

    @property
    def root(self) -> Path:
        return self._root

    def save(self, state: TurnState) -> None:
        """原子保存 prompt，并拒绝同一轮次的不同 payload。"""

        self._ensure_root()
        path = self._path(state.session_id, state.turn_id)
        existing = self.load(state.session_id, state.turn_id)
        if existing is not None:
            if existing.prompt != state.prompt:
                raise TurnStateConflictError("turn_state_payload_conflict")
            return
        self._write(state, path)

    def load(self, session_id: str, turn_id: str) -> TurnState | None:
        """精确读取并校验标识，绝不按模糊会话状态回退。"""

        path = self._path(session_id, turn_id)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            state = TurnState.model_validate_json(raw)
        except ValueError as exc:
            raise TurnStateError("invalid_turn_state") from exc
        # 兼容历史版本：v1（仅 prompt）与 v2（含 capture）均允许读取，
        # extra="ignore" 丢弃旧 capture 字段。
        if state.schema_version not in _SUPPORTED_STATE_SCHEMA_VERSIONS:
            raise TurnStateError("unsupported_turn_state_version")
        if state.session_id != session_id or state.turn_id != turn_id:
            raise TurnStateError("turn_state_identifier_mismatch")
        return state

    def delete(self, session_id: str, turn_id: str) -> None:
        """删除一个精确轮次状态；重复删除保持幂等。"""

        self._path(session_id, turn_id).unlink(missing_ok=True)

    def cleanup_expired(self) -> int:
        """清除过期或损坏状态；损坏文件记日志后清理，不单独记录其正文。"""

        if not self._root.exists():
            return 0
        cutoff = self._now() - self._ttl
        removed = 0
        for path in self._root.glob("*.json"):
            try:
                state = TurnState.model_validate_json(
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

    def _path(self, session_id: str, turn_id: str) -> Path:
        digest = hashlib.sha256(
            f"{session_id}\x1f{turn_id}".encode()
        ).hexdigest()
        return self._root / f"{digest}.json"

    def _ensure_root(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        # 仅在创建时收紧权限；已存在目录保持原权限。
        if hasattr(os, "chmod"):
            mode = self._root.stat().st_mode & 0o777
            if mode & 0o077:
                os.chmod(self._root, mode & 0o700)

    def _write(self, state: TurnState, path: Path) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._root,
            prefix=".turn-",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            # os.fchmod 在 Windows 不存在（AttributeError）；mkstemp 在所有平台
            # 都按 0600 创建文件描述符，POSIX 上再显式收紧一次。
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                json.dump(
                    state.model_dump(mode="json"),
                    stream,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
            if hasattr(os, "chmod"):
                os.chmod(path, 0o600)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)
