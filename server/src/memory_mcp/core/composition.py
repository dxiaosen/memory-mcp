"""Memory Core 的最小依赖组装。"""

from collections.abc import Iterable

from memory_mcp.core.adapters.sensitive import RegexSensitiveContentGuard
from memory_mcp.core.application import MemoryService
from memory_mcp.core.ports import (
    CandidateExtractor,
    MemoryProfile,
    MemoryRepository,
    ProfileRegistry,
    SensitiveContentGuard,
)


def create_memory_service(
    repository: MemoryRepository,
    profiles: Iterable[MemoryProfile],
    *,
    candidate_extractor: CandidateExtractor | None = None,
    sensitive_guard: SensitiveContentGuard | None = None,
) -> MemoryService:
    """创建服务并显式注册全部记忆配置。"""

    service = MemoryService(
        repository,
        ProfileRegistry(),
        candidate_extractor=candidate_extractor,
        sensitive_guard=sensitive_guard or RegexSensitiveContentGuard(),
    )
    for profile in profiles:
        service.register_profile(profile)
    return service
