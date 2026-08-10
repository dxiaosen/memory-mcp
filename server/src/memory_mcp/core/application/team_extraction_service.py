"""团队公共记忆自动提取服务。"""

import logging
from collections.abc import Callable
from datetime import datetime
from time import perf_counter

from memory_mcp.core.domain import TeamExtractionResult
from memory_mcp.core.ports import MemoryRepository, ProfileRegistry
from memory_mcp.core.support import log_event, stable_reference

_LOGGER = logging.getLogger(__name__)

DEFAULT_SIMILARITY_THRESHOLD = 0.70
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
        # 按 team_owner_id 去重并合并成员：避免按成员重复产生同一 team owner 导致
        # batch team_count 虚高、同一团队被反复提取。
        self._team_configs = _dedup_team_configs(team_configs or ())
        self._similarity_threshold = similarity_threshold
        self._min_cluster_size = min_cluster_size

    def run_once(self) -> tuple[TeamExtractionResult, ...]:
        """对每个配置的团队执行一次共性提取。"""

        started_at = perf_counter()
        effective_at = self._clock()
        results: list[TeamExtractionResult] = []
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.team_extraction.batch_started",
            team_count=len(self._team_configs),
        )
        for team_owner_id, member_owner_ids, profile_id in self._team_configs:
            try:
                result = self._repository.extract_team_common_memories(
                    team_owner_id=team_owner_id,
                    member_owner_ids=member_owner_ids,
                    profile_id=profile_id,
                    effective_at=effective_at,
                    similarity_threshold=self._similarity_threshold,
                    min_cluster_size=self._min_cluster_size,
                )
            except Exception as exc:
                log_event(
                    _LOGGER,
                    logging.ERROR,
                    "memory.team_extraction.team_failed",
                    team_owner_ref=stable_reference(team_owner_id),
                    profile_id=profile_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                continue
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


def _dedup_team_configs(
    team_configs: tuple[tuple[str, tuple[str, ...], str], ...],
) -> tuple[tuple[str, tuple[str, ...], str], ...]:
    """按 team_owner_id 去重，合并同 team 的 member_owner_ids（并集保序）。

    同一 team_owner_id 可能因按成员展开而重复出现，batch 前需合并，否则 team_count 虚高
    且同一团队被反复提取。同一 team_owner_id 取首个 profile_id，members 取并集并保持首次出现顺序。
    """

    members_by_team: dict[str, list[str]] = {}
    profile_by_team: dict[str, str] = {}
    order: list[str] = []
    for team_owner_id, member_owner_ids, profile_id in team_configs:
        if team_owner_id not in members_by_team:
            members_by_team[team_owner_id] = []
            profile_by_team[team_owner_id] = profile_id
            order.append(team_owner_id)
        seen = set(members_by_team[team_owner_id])
        for member in member_owner_ids:
            if member not in seen:
                seen.add(member)
                members_by_team[team_owner_id].append(member)
    return tuple(
        (team_id, tuple(members_by_team[team_id]), profile_by_team[team_id])
        for team_id in order
    )
