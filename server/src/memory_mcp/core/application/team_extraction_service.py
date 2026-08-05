"""团队公共记忆自动提取服务。"""

import logging
from collections.abc import Callable
from datetime import datetime
from time import perf_counter

from memory_mcp.core.domain import TeamExtractionResult
from memory_mcp.core.ports import MemoryRepository, ProfileRegistry
from memory_mcp.logging import log_event, stable_reference

_LOGGER = logging.getLogger(__name__)

DEFAULT_SIMILARITY_THRESHOLD = 0.85
DEFAULT_MIN_CLUSTER_SIZE = 2


class TeamExtractionService:
    """定时扫描团队成员个人记忆，用 embedding 聚类提取公共知识候选。

    提取出的共性候选写入团队 pending review，由团队成员人工确认后
    变为团队公共记忆。不做自动确认——人决定哪些值得沉淀为团队知识。
    """

    def __init__(
        self,
        repository: MemoryRepository,
        profile_registry: ProfileRegistry,
        *,
        clock: Callable[[], datetime],
        team_configs: tuple[tuple[str, tuple[str, ...], str], ...] | None = None,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    ) -> None:
        self._repository = repository
        self._profile_registry = profile_registry
        self._clock = clock
        self._team_configs = team_configs or ()
        self._similarity_threshold = similarity_threshold
        self._min_cluster_size = min_cluster_size

    def run_once(self) -> tuple[TeamExtractionResult, ...]:
        """对每个配置的团队执行一次共性提取。"""

        started_at = perf_counter()
        effective_at = self._clock()
        results: list[TeamExtractionResult] = []
        for team_owner_id, member_owner_ids, profile_id in self._team_configs:
            result = self._repository.extract_team_common_memories(
                team_owner_id=team_owner_id,
                member_owner_ids=member_owner_ids,
                profile_id=profile_id,
                effective_at=effective_at,
                similarity_threshold=self._similarity_threshold,
                min_cluster_size=self._min_cluster_size,
            )
            results.append(result)
            log_event(
                _LOGGER,
                logging.INFO,
                "memory.team_extraction.completed",
                team_owner_ref=stable_reference(team_owner_id),
                member_count=result.member_count,
                memory_count=result.memory_count,
                cluster_count=result.cluster_count,
                candidate_count=result.candidate_count,
            )
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.team_extraction.batch_completed",
            duration_ms=round((perf_counter() - started_at) * 1000, 3),
            team_count=len(results),
            total_candidates=sum(r.candidate_count for r in results),
        )
        return tuple(results)
