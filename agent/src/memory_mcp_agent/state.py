"""独立 Agent Hook 进程之间的短期轮次状态。"""

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
# 开发阶段：重置为首个 schema 版本。仍保留 "2" 在支持集合内，保证本地残留的
# 旧版本状态文件在 24h TTL 内可被平滑读出而不报错；新写入一律标 "1"。
_SUPPORTED_STATE_SCHEMA_VERSIONS = frozenset({_STATE_SCHEMA_VERSION, "2"})
_DEFAULT_TTL = timedelta(hours=24)


class TurnStateError(ValueError):
    """本地轮次状态损坏或与请求不一致。"""


class TurnStateConflictError(TurnStateError):
    """同一个宿主轮次标识被不同用户输入重用。"""


class TurnState(BaseModel):
    """本地 outbox 中单轮次的最小持久状态：BeforeRun 存 prompt，AfterRun 追加 capture 字段。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = _STATE_SCHEMA_VERSION
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    profile_id: str | None = Field(default=None, min_length=1)
    final_output: str | None = Field(default=None, min_length=1)
    capture_observed_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("created_at")
    @classmethod
    def require_aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value

    @field_validator("capture_observed_at")
    @classmethod
    def require_aware_capture_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("capture_observed_at must be timezone-aware")
        return value

    def model_post_init(self, __context: object) -> None:
        if (self.final_output is None) != (self.capture_observed_at is None):
            raise ValueError(
                "final_output and capture_observed_at must be supplied together"
            )

    @property
    def capture_pending(self) -> bool:
        return self.final_output is not None


class TurnStateStore:
    """本地 outbox：使用摘要文件名和原子替换保存短期轮次状态。

    每个 BeforeRun 写入 prompt，AfterRun 把 final_output 和 observed_at
    原子追加到同一文件；投递成功后删除，失败则保留待下次 Stop 重投。
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

    def stage_capture(
        self,
        session_id: str,
        turn_id: str,
        *,
        final_output: str,
        observed_at: datetime,
        profile_id: str | None,
    ) -> TurnState:
        """在网络调用前原子保存固定的完整捕获 payload，保证 outbox 可重投。"""

        existing = self.load(session_id, turn_id)
        if existing is None:
            raise TurnStateError("missing_turn_state")
        if existing.capture_pending:
            if (
                existing.final_output != final_output
                or existing.profile_id != profile_id
            ):
                raise TurnStateConflictError("turn_capture_payload_conflict")
            return existing
        staged = TurnState.model_validate(
            {
                **existing.model_dump(),
                "schema_version": _STATE_SCHEMA_VERSION,
                "profile_id": profile_id,
                "final_output": final_output,
                "capture_observed_at": observed_at,
            }
        )
        self._ensure_root()
        self._write(staged, self._path(session_id, turn_id))
        return staged

    def pending_captures(
        self,
        *,
        exclude: tuple[str, str] | None = None,
        limit: int = 1,
    ) -> tuple[TurnState, ...]:
        """按创建时间返回有限待投递项，不记录或复制正文到日志。"""

        if limit < 1:
            raise ValueError("limit must be positive")
        if not self._root.exists():
            return ()
        pending: list[TurnState] = []
        for path in self._root.glob("*.json"):
            try:
                state = TurnState.model_validate_json(path.read_text(encoding="utf-8"))
            except OSError as exc:
                log_event(
                    _LOGGER,
                    logging.WARNING,
                    "turn_state.read_failed",
                    path=str(path),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                continue
            except ValueError as exc:
                log_event(
                    _LOGGER,
                    logging.WARNING,
                    "turn_state.invalid",
                    path=str(path),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                continue
            if not state.capture_pending:
                continue
            if exclude == (state.session_id, state.turn_id):
                continue
            pending.append(state)
        return tuple(
            sorted(
                pending,
                key=lambda state: (
                    state.capture_observed_at,
                    state.created_at,
                    state.session_id,
                    state.turn_id,
                ),
            )[:limit]
        )

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
        # 兼容历史版本：v1（仅 prompt）与 v2（含 capture）均允许读取。
        if state.schema_version not in _SUPPORTED_STATE_SCHEMA_VERSIONS:
            raise TurnStateError("unsupported_turn_state_version")
        if state.session_id != session_id or state.turn_id != turn_id:
            raise TurnStateError("turn_state_identifier_mismatch")
        return state

    def delete(self, session_id: str, turn_id: str) -> None:
        """删除一个精确轮次状态；重复删除保持幂等。"""

        self._path(session_id, turn_id).unlink(missing_ok=True)

    def cleanup_expired(self) -> int:
        """清除过期或损坏状态；损坏文件记日志后清理，不单独记录其 prompt。"""

        if not self._root.exists():
            return 0
        cutoff = self._now() - self._ttl
        removed = 0
        for path in self._root.glob("*.json"):
            try:
                state = TurnState.model_validate_json(path.read_text(encoding="utf-8"))
                expired = state.created_at < cutoff
            except (OSError, ValueError) as exc:
                # 损坏文件当过期处理删除，但必须留痕，否则捕获静默丢失。
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

    def _ensure_root(self) -> None:
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Windows 无 POSIX 权限位，os.chmod 对 mode 的设置无效果但可调用；
        # 仍守卫一下避免极少数受限环境抛错。
        if hasattr(os, "chmod"):
            os.chmod(self._root, 0o700)

    def _path(self, session_id: str, turn_id: str) -> Path:
        """用 session_id/turn_id 的摘要作为文件名，避免原始标识泄露到文件系统。"""

        digest = hashlib.sha256(f"{session_id}\x1f{turn_id}".encode()).hexdigest()
        return self._root / f"{digest}.json"
