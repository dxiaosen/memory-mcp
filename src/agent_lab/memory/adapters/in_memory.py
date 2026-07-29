"""供离线契约测试和演示使用的进程内 Repository。"""

from collections.abc import Sequence
from uuid import UUID

from agent_lab.memory.domain import (
    LifecycleStatus,
    MemoryRecord,
    PrincipalContext,
)
from agent_lab.memory.exceptions import (
    InvalidMemoryTypeError,
    ScenarioNotRegisteredError,
)
from agent_lab.memory.ports import ScenarioPolicy


class InMemoryMemoryRepository:
    """严格模拟 owner 范围和场景类型约束，不作为生产存储。"""

    def __init__(self) -> None:
        self._records: dict[UUID, MemoryRecord] = {}
        self._scenario_types: dict[str, frozenset[str]] = {}

    def register_scenario(self, policy: ScenarioPolicy) -> None:
        self._scenario_types[policy.scenario_id] = frozenset(policy.memory_types)

    def add(
        self,
        principal: PrincipalContext,
        record: MemoryRecord,
    ) -> None:
        if record.item.owner_id != principal.owner_id:
            raise ValueError("record owner must match trusted principal")
        scenario_types = self._scenario_types.get(record.item.scenario)
        if scenario_types is None:
            raise ScenarioNotRegisteredError(
                f"scenario is not registered: {record.item.scenario}"
            )
        if record.item.memory_type not in scenario_types:
            raise InvalidMemoryTypeError(
                "memory type is not registered for scenario "
                f"{record.item.scenario}: {record.item.memory_type}"
            )
        if record.item.memory_id in self._records:
            raise ValueError("memory_id must be unique")
        self._records[record.item.memory_id] = record

    def get(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> MemoryRecord | None:
        record = self._records.get(memory_id)
        if record is None or record.item.owner_id != principal.owner_id:
            return None
        return record

    def list(
        self,
        principal: PrincipalContext,
        *,
        active_only: bool,
    ) -> Sequence[MemoryRecord]:
        records = (
            record
            for record in self._records.values()
            if record.item.owner_id == principal.owner_id
        )
        if active_only:
            records = (
                record
                for record in records
                if record.current_revision.lifecycle_status is LifecycleStatus.ACTIVE
            )
        return tuple(sorted(records, key=lambda value: value.item.created_at))
