"""服务端内部的有界记忆维护用例：过期清理与待确认回收。"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from time import perf_counter
from uuid import UUID, uuid4

from memory_mcp.core.domain import (
    AssertionKind,
    Evidence,
    EvidenceSourceType,
    LifecycleStatus,
    MaintenanceResult,
    MemoryItem,
    MemoryRecord,
    MemoryRevision,
    PrincipalContext,
    VerificationStatus,
)
from memory_mcp.core.ports import (
    MemoryMetadataPolicy,
    MemoryRepository,
    ProfileRegistry,
)
from memory_mcp.core.support import log_event, stable_reference

_LOGGER = logging.getLogger(__name__)

MAINTENANCE_BATCH_SIZE = 500
PENDING_REVIEW_RETENTION = timedelta(days=30)
# 系统提醒记忆的来源标记：conversation_id 前缀与 source_turn_id，用于追溯。
_SYSTEM_REMINDER_CONVERSATION = "system:maintenance"
_SYSTEM_REMINDER_TURN_ID = "expired-evidence-reminder"


class MemoryMaintenanceService:
    """按可信时间执行一次有界维护批次：清理过期记忆、关系与超期待确认项。

    清理后若 Profile 声明了 ``expiry_derivations``，对因端点过期而失效的关系
    派生一条 ``ongoing_research`` 提醒记忆（如"支撑论点的证据已过期，需复核"），
    促使用户重新审视原论点是否仍成立。同一 owner + 同一 focus thesis 的提醒
    去重：已存在同 subject 的活动 ongoing_research 时跳过，避免重复提醒。
    """

    def __init__(
        self,
        repository: MemoryRepository,
        profile_registry: ProfileRegistry,
        *,
        clock: Callable[[], datetime],
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._repository = repository
        self._profile_registry = profile_registry
        self._clock = clock
        self._id_factory = id_factory

    def run_once(self) -> MaintenanceResult:
        """执行一批维护操作，返回过期与回收计数。"""
        started_at = perf_counter()
        effective_at = self._clock()
        result = self._repository.maintain(
            effective_at=effective_at,
            review_cutoff=effective_at - PENDING_REVIEW_RETENTION,
            limit=MAINTENANCE_BATCH_SIZE,
        )
        reminder_count = self._derive_expired_evidence_reminders(
            result, effective_at
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
            expired_relation_context_count=len(result.expired_relation_contexts),
            reminder_count=reminder_count,
        )
        return result

    def _derive_expired_evidence_reminders(
        self,
        result: MaintenanceResult,
        effective_at: datetime,
    ) -> int:
        """对失效关系按 Profile.expiry_derivations 派生提醒记忆，返回写入数。"""

        if not result.expired_relation_contexts:
            return 0
        written = 0
        for context in result.expired_relation_contexts:
            try:
                profile = self._profile_registry.get(context.profile_id)
            except Exception as exc:
                log_event(
                    _LOGGER,
                    logging.WARNING,
                    "memory.maintenance.reminder_skipped",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    profile_id=context.profile_id,
                    relation_type=context.relation_type,
                    focus_memory_id=str(context.focus_memory_id),
                )
                continue
            derivation = _match_derivation(profile.expiry_derivations, context.relation_type)
            if derivation is None:
                continue
            metadata_policy = profile.metadata_policies.get(
                derivation.reminder_memory_type
            )
            if metadata_policy is None:
                continue
            principal = PrincipalContext(context.owner_id)
            if self._has_active_reminder(
                principal,
                profile_id=context.profile_id,
                subject=context.focus_subject,
                memory_type=derivation.reminder_memory_type,
                effective_at=effective_at,
            ):
                continue
            self._write_reminder(
                principal,
                context=context,
                derivation=derivation,
                metadata_policy=metadata_policy,
                effective_at=effective_at,
            )
            written += 1
        return written

    def _has_active_reminder(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        subject: str,
        memory_type: str,
        effective_at: datetime,
    ) -> bool:
        """同 owner + 同 focus thesis + 同 reminder 类型已有活动记忆则跳过。"""

        existing = self._repository.find_current(
            principal,
            profile_id=profile_id,
            subject=subject,
            memory_type=memory_type,
            effective_at=effective_at,
            limit=1,
        )
        return len(existing) > 0

    def _write_reminder(
        self,
        principal: PrincipalContext,
        *,
        context,
        derivation,
        metadata_policy: MemoryMetadataPolicy,
        effective_at: datetime,
    ) -> None:
        """构造并写入一条 ongoing_research 提醒记忆。"""

        content = derivation.reminder_template.format(
            endpoint_subject=context.expired_subject,
            thesis_subject=context.focus_subject,
        )
        memory_id = self._id_factory()
        revision_id = self._id_factory()
        record = MemoryRecord(
            item=MemoryItem(
                memory_id=memory_id,
                owner_id=principal.owner_id,
                profile_id=context.profile_id,
                subject=context.focus_subject,
                memory_type=derivation.reminder_memory_type,
                created_at=effective_at,
            ),
            current_revision=MemoryRevision(
                revision_id=revision_id,
                memory_id=memory_id,
                owner_id=principal.owner_id,
                revision_number=1,
                content=content,
                assertion_kind=AssertionKind.USER_VIEW,
                lifecycle_status=LifecycleStatus.ACTIVE,
                business_progress="monitoring",
                save_rationale="系统提醒：过期证据依赖链复核",
                observed_at=effective_at,
                created_at=effective_at,
                extraction_confidence=None,
                verification_status=VerificationStatus.USER_ASSERTED,
                sensitivity_level=metadata_policy.sensitivity_level,
                valid_from=effective_at,
                valid_until=(
                    effective_at + timedelta(days=metadata_policy.validity_days)
                    if metadata_policy.validity_days is not None
                    else None
                ),
            ),
            evidence=(
                Evidence(
                    evidence_id=self._id_factory(),
                    memory_id=memory_id,
                    revision_id=revision_id,
                    owner_id=principal.owner_id,
                    conversation_id=_SYSTEM_REMINDER_CONVERSATION,
                    source_turn_id=_SYSTEM_REMINDER_TURN_ID,
                    source_expression=content,
                    observed_at=effective_at,
                    created_at=effective_at,
                    source_type=EvidenceSourceType.SYSTEM,
                ),
            ),
        )
        self._repository.add(principal, record)
        log_event(
            _LOGGER,
            logging.INFO,
            "memory.maintenance.reminder_written",
            owner_ref=stable_reference(principal.owner_id),
            profile_id=context.profile_id,
            relation_type=context.relation_type,
            focus_memory_id=str(context.focus_memory_id),
            reminder_memory_type=derivation.reminder_memory_type,
        )


def _match_derivation(derivations, relation_type: str):
    """返回 trigger_relation_types 包含 relation_type 的首条派生规则。"""

    for derivation in derivations.values():
        if relation_type in derivation.trigger_relation_types:
            return derivation
    return None
