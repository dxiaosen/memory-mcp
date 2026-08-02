"""Memory Core 的最小依赖组装。"""

from collections.abc import Iterable

from memory_mcp.core.adapters.sensitive import RegexSensitiveContentGuard
from memory_mcp.core.application import MemoryService
from memory_mcp.core.ports import (
    CandidateExtractor,
    MemoryProfile,
    MemoryRepository,
    ProfileRegistry,
    RelationExtractor,
    SensitiveContentGuard,
)


def create_memory_service(
    repository: MemoryRepository,
    profiles: Iterable[MemoryProfile],
    *,
    candidate_extractor: CandidateExtractor | None = None,
    relation_extractor: RelationExtractor | None = None,
    sensitive_guard: SensitiveContentGuard | None = None,
    recall_candidate_limit: int = 500,
) -> MemoryService:
    """创建服务并显式注册全部记忆配置。"""

    service = MemoryService(
        repository,
        ProfileRegistry(),
        candidate_extractor=candidate_extractor,
        relation_extractor=relation_extractor,
        sensitive_guard=sensitive_guard or RegexSensitiveContentGuard(),
        recall_candidate_limit=recall_candidate_limit,
    )
    for profile in profiles:
        service.register_profile(profile)
    return service
