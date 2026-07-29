"""通用记忆持久化端口。"""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from agent_lab.memory.domain import MemoryRecord, PrincipalContext
from agent_lab.memory.ports.scenarios import ScenarioPolicy


class MemoryRepository(Protocol):
    """所有业务读写都必须显式携带可信 owner 上下文。"""

    def register_scenario(self, policy: ScenarioPolicy) -> None:
        """将场景及合法类型登记到持久化约束中。"""

        ...

    def add(
        self,
        principal: PrincipalContext,
        record: MemoryRecord,
    ) -> None:
        """原子保存一张包含当前 revision 和来源的记忆卡片。"""

        ...

    def get(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> MemoryRecord | None:
        """读取当前用户拥有的指定记忆；越权与不存在都返回 ``None``。"""

        ...

    def list(
        self,
        principal: PrincipalContext,
        *,
        active_only: bool,
    ) -> Sequence[MemoryRecord]:
        """列出当前用户的当前版本，并可排除非活动记忆。"""

        ...
