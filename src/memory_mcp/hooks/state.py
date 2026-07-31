"""独立 Agent Hook 进程之间的短期轮次状态。"""

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

_STATE_SCHEMA_VERSION = "1"
_DEFAULT_TTL = timedelta(hours=24)


class TurnStateError(ValueError):
    """本地轮次状态损坏或与请求不一致。"""


class TurnStateConflictError(TurnStateError):
    """同一个宿主轮次标识被不同用户输入重用。"""


class TurnState(BaseModel):
    """Stop 捕获所需的最小持久状态。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = _STATE_SCHEMA_VERSION
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("created_at")
    @classmethod
    def require_aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class TurnStateStore:
    """使用摘要文件名和原子替换保存短期轮次状态。"""

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

        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._root,
            prefix=".turn-",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
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
        if state.schema_version != _STATE_SCHEMA_VERSION:
            raise TurnStateError("unsupported_turn_state_version")
        if state.session_id != session_id or state.turn_id != turn_id:
            raise TurnStateError("turn_state_identifier_mismatch")
        return state

    def delete(self, session_id: str, turn_id: str) -> None:
        """删除一个精确轮次状态；重复删除保持幂等。"""

        self._path(session_id, turn_id).unlink(missing_ok=True)

    def cleanup_expired(self) -> int:
        """清除过期或损坏状态，不读取或记录其 prompt。"""

        if not self._root.exists():
            return 0
        cutoff = self._now() - self._ttl
        removed = 0
        for path in self._root.glob("*.json"):
            try:
                state = TurnState.model_validate_json(path.read_text(encoding="utf-8"))
                expired = state.created_at < cutoff
            except OSError, ValueError:
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
        os.chmod(self._root, 0o700)

    def _path(self, session_id: str, turn_id: str) -> Path:
        digest = hashlib.sha256(f"{session_id}\x1f{turn_id}".encode()).hexdigest()
        return self._root / f"{digest}.json"
