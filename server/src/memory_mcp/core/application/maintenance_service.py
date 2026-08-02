"""服务端内部的有界记忆维护用例。"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from time import perf_counter

from memory_mcp.core.domain import MaintenanceResult
from memory_mcp.core.ports import MemoryRepository
from memory_mcp.logging import log_event

_LOGGER = logging.getLogger(__name__)

MAINTENANCE_BATCH_SIZE = 500
PENDING_REVIEW_RETENTION = timedelta(days=30)


class MemoryMaintenanceService:
    """按可信时间执行一次跨 owner、无正文的维护批次。"""

    def __init__(
        self,
        repository: MemoryRepository,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._clock = clock

    def run_once(self) -> MaintenanceResult:
        started_at = perf_counter()
        effective_at = self._clock()
        result = self._repository.maintain(
            effective_at=effective_at,
            review_cutoff=effective_at - PENDING_REVIEW_RETENTION,
            limit=MAINTENANCE_BATCH_SIZE,
        )
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.maintenance.completed",
            duration_ms=round((perf_counter() - started_at) * 1000, 3),
            expired_memory_count=result.expired_memory_count,
            expired_review_count=result.expired_review_count,
            has_more=result.has_more,
            stale_relation_count=result.stale_relation_count,
        )
        return result
