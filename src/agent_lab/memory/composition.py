"""Memory Core 的最小依赖组装。"""

from collections.abc import Iterable

from agent_lab.memory.application import MemoryService
from agent_lab.memory.ports import (
    MemoryRepository,
    ScenarioPolicy,
    ScenarioRegistry,
)


def create_memory_service(
    repository: MemoryRepository,
    policies: Iterable[ScenarioPolicy],
) -> MemoryService:
    """创建服务并显式注册全部场景策略。"""

    service = MemoryService(repository, ScenarioRegistry())
    for policy in policies:
        service.register_scenario(policy)
    return service
