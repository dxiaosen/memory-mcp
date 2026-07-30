"""Memory Core 的最小依赖组装。"""

from collections.abc import Iterable

from agent_lab.memory.adapters.sensitive import RegexSensitiveContentGuard
from agent_lab.memory.application import MemoryService
from agent_lab.memory.ports import (
    CandidateExtractor,
    MemoryRepository,
    ScenarioPolicy,
    ScenarioRegistry,
    SensitiveContentGuard,
)


def create_memory_service(
    repository: MemoryRepository,
    policies: Iterable[ScenarioPolicy],
    *,
    candidate_extractor: CandidateExtractor | None = None,
    sensitive_guard: SensitiveContentGuard | None = None,
) -> MemoryService:
    """创建服务并显式注册全部场景策略。"""

    service = MemoryService(
        repository,
        ScenarioRegistry(),
        candidate_extractor=candidate_extractor,
        sensitive_guard=sensitive_guard or RegexSensitiveContentGuard(),
    )
    for policy in policies:
        service.register_scenario(policy)
    return service
