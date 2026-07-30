"""Memory Core 的最小依赖组装。"""

from collections.abc import Iterable

from memory_mcp.core.adapters.sensitive import RegexSensitiveContentGuard
from memory_mcp.core.application import MemoryService
from memory_mcp.core.ports import (
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
