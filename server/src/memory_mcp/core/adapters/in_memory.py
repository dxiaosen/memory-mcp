"""供离线契约测试和演示使用的进程内 Repository。"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import UUID, uuid4

from memory_mcp.core.domain import (
    AssertionKind,
    Candidate,
    CandidateDurability,
    CaptureResult,
    CaptureStatus,
    Evidence,
    EvidenceDocument,
    EvidenceSourceType,
    ExpiredRelationContext,
    ExpressionBasis,
    LifecycleStatus,
    MaintenanceResult,
    MemoryHistoryEntry,
    MemoryRecallCandidate,
    MemoryRecord,
    MemoryRelation,
    MemoryRelationSummary,
    MemoryRevision,
    PrincipalContext,
    RelationDirection,
    RelationOrigin,
    RelationScope,
    RelationStatus,
    ReviewItem,
    ReviewStatus,
    SensitivityLevel,
    TeamExtractionResult,
    VerificationStatus,
    format_divergence_rationale,
    has_conflicting_business_progress,
    normalize_memory_text,
    select_cluster_content,
    select_cluster_subject,
)
from memory_mcp.core.exceptions import (
    IdempotencyConflictError,
    InvalidMemoryTypeError,
    ProfileNotRegisteredError,
)
from memory_mcp.core.ports import (
    CaptureEnqueueWrite,
    CaptureWrite,
    DuplicateEvidenceWrite,
    MemoryProfile,
    MemoryRelationPolicy,
    PendingCapture,
    RecallCandidateSet,
    ReplacementWrite,
)


class InMemoryMemoryRepository:
    """严格模拟 owner 范围和记忆配置类型约束，不作为生产存储。

    读取方法用 ``principal.visible_owner_ids`` 集合过滤（支持团队可见记忆），
    写入校验允许 ``record.item.owner_id`` 是集合内任意值（团队提升路径），
    与 PostgreSQL 读取用 ``ANY(%s)`` 集合、写入用单值 ``= %s`` 的约定一致。
    """

    def __init__(self) -> None:
        self._records: dict[UUID, MemoryRecord] = {}
        self._history: dict[UUID, tuple[MemoryHistoryEntry, ...]] = {}
        self._profile_types: dict[str, frozenset[str]] = {}
        self._profile_relation_policies: dict[
            str,
            dict[str, MemoryRelationPolicy],
        ] = {}
        self._captures: dict[
            tuple[str, ...],
            tuple[CaptureResult, str, str | None],
        ] = {}
        self._reviews: dict[UUID, ReviewItem] = {}
        self._relations: dict[UUID, MemoryRelation] = {}
        self._capture_lock = Lock()
        self._relation_lock = Lock()
        self._id_factory = uuid4
        self._team_extraction_runs: dict[tuple[str, str, datetime], TeamExtractionResult] = {}

    def register_profile(self, profile: MemoryProfile) -> None:
        """注册 profile 的 memory_type 和 relation policy 配置。"""

        self._profile_types[profile.profile_id] = frozenset(profile.memory_types)
        self._profile_relation_policies[profile.profile_id] = dict(
            profile.relation_policies
        )

    def add(
        self,
        principal: PrincipalContext,
        record: MemoryRecord,
    ) -> None:
        # _validate_record 允许 record.owner_id 是 visible_owner_ids 集合内
        # 任意值（团队提升），与读取用集合过滤的约定一致。
        self._validate_record(principal, record)
        if record.item.memory_id in self._records:
            raise ValueError("memory_id must be unique")
        self._records[record.item.memory_id] = record
        self._history[record.item.memory_id] = (
            MemoryHistoryEntry(
                revision=record.current_revision,
                evidence=record.evidence,
            ),
        )

    def get(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> MemoryRecord | None:
        """按可见 owner 集合读取单条记忆，不可见时返回 None。"""

        record = self._records.get(memory_id)
        if record is None or record.item.owner_id not in principal.visible_owner_ids:
            return None
        return record

    def list(
        self,
        principal: PrincipalContext,
        *,
        active_only: bool,
        effective_at: datetime | None = None,
    ) -> Sequence[MemoryRecord]:
        """列出可见 owner 集合内的全部当前记忆。"""

        owner_ids = principal.visible_owner_ids
        records = (
            record
            for record in self._records.values()
            if record.item.owner_id in owner_ids
        )
        if active_only:
            resolved_time = effective_at or datetime.now(UTC)
            records = (
                record
                for record in records
                if record.current_revision.lifecycle_status is LifecycleStatus.ACTIVE
                and _is_effective(record.current_revision, resolved_time)
            )
        return tuple(sorted(records, key=lambda value: value.item.created_at))

    def find_current(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        subject: str | None = None,
        memory_type: str | None = None,
        effective_at: datetime | None = None,
        limit: int | None = None,
    ) -> Sequence[MemoryRecord]:
        """先按可信 owner 和活动 current 集合收窄，再做规范化 subject 匹配。"""

        subject_key = normalize_memory_text(subject) if subject is not None else None
        resolved_time = effective_at or datetime.now(UTC)
        owner_ids = principal.visible_owner_ids
        records = (
            record
            for record in self._records.values()
            if record.item.owner_id in owner_ids
            and record.item.profile_id == profile_id
            and record.current_revision.lifecycle_status is LifecycleStatus.ACTIVE
            and _is_effective(record.current_revision, resolved_time)
            and (memory_type is None or record.item.memory_type == memory_type)
            and (
                subject_key is None
                or normalize_memory_text(record.item.subject) == subject_key
            )
        )
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        ordered = sorted(
            records,
            key=lambda value: (
                value.current_revision.observed_at,
                value.item.memory_id,
            ),
            reverse=True,
        )
        return tuple(ordered[:limit] if limit is not None else ordered)

    def find_semantically_similar(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        memory_type: str,
        embedding: Sequence[float],
        threshold: float,
        effective_at: datetime,
    ) -> MemoryRecord | None:
        """按余弦相似度在同 profile+type 活动记忆中找首条超阈值的命中。"""

        query = tuple(embedding)
        owner_ids = principal.visible_owner_ids
        best: tuple[float, MemoryRecord] | None = None
        for record in self._records.values():
            if (
                record.item.owner_id not in owner_ids
                or record.item.profile_id != profile_id
                or record.item.memory_type != memory_type
                or record.current_revision.lifecycle_status
                is not LifecycleStatus.ACTIVE
                or not _is_effective(record.current_revision, effective_at)
            ):
                continue
            similarity = _cosine_similarity(
                record.current_revision.embedding,
                query,
            )
            if similarity >= threshold and (
                best is None or similarity > best[0]
            ):
                best = (similarity, record)
        return best[1] if best is not None else None

    def find_assistant_echo(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        embedding: Sequence[float],
        threshold: float,
        effective_at: datetime,
    ) -> MemoryRecord | None:
        """跨 memory_type 查 assistant 回声：不限 memory_type 的最高相似度命中。"""

        query = tuple(embedding)
        owner_ids = principal.visible_owner_ids
        best: tuple[float, MemoryRecord] | None = None
        for record in self._records.values():
            if (
                record.item.owner_id not in owner_ids
                or record.item.profile_id != profile_id
                or record.current_revision.lifecycle_status
                is not LifecycleStatus.ACTIVE
                or not _is_effective(record.current_revision, effective_at)
            ):
                continue
            similarity = _cosine_similarity(
                record.current_revision.embedding,
                query,
            )
            if similarity >= threshold and (
                best is None or similarity > best[0]
            ):
                best = (similarity, record)
        return best[1] if best is not None else None

    def find_semantically_similar_top2(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        memory_type: str,
        embedding: Sequence[float],
        threshold: float,
        effective_at: datetime,
    ) -> tuple[
        tuple[float, MemoryRecord] | None,
        tuple[float, MemoryRecord] | None,
    ]:
        """返回相似度最高的两条活动记忆及其相似度。"""

        query = tuple(embedding)
        owner_ids = principal.visible_owner_ids
        top1: tuple[float, MemoryRecord] | None = None
        top2: tuple[float, MemoryRecord] | None = None
        for record in self._records.values():
            if (
                record.item.owner_id not in owner_ids
                or record.item.profile_id != profile_id
                or record.item.memory_type != memory_type
                or record.current_revision.lifecycle_status
                is not LifecycleStatus.ACTIVE
                or not _is_effective(record.current_revision, effective_at)
            ):
                continue
            similarity = _cosine_similarity(
                record.current_revision.embedding,
                query,
            )
            if similarity < threshold:
                continue
            if top1 is None or similarity > top1[0]:
                top2 = top1
                top1 = (similarity, record)
            elif top2 is None or similarity > top2[0]:
                top2 = (similarity, record)
        return top1, top2

    def find_recall_candidates(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        search_text: str,
        subject: str | None,
        effective_at: datetime,
        limit: int,
        query_embedding=None,
    ) -> RecallCandidateSet:
        """模拟 PostgreSQL 的 lexical/vector/recent 三路召回。"""

        if limit < 1:
            raise ValueError("limit must be positive")
        normalized_search = normalize_memory_text(search_text)
        if not normalized_search:
            raise ValueError("search_text must not be empty")
        eligible = tuple(
            self.find_current(
                principal,
                profile_id=profile_id,
                subject=subject,
                effective_at=effective_at,
            )
        )
        lexical_limit = (
            1 if limit == 1 else min(limit - 1, max(1, (limit * 7 + 9) // 10))
        )
        scored = tuple(
            (
                max(
                    _trigram_similarity(search_text, record.item.subject),
                    _trigram_similarity(
                        search_text,
                        record.current_revision.content,
                    ),
                ),
                record,
            )
            for record in eligible
        )
        lexical = tuple(
            record
            for score, record in sorted(
                (value for value in scored if value[0] >= 0.08),
                key=lambda value: (
                    value[0],
                    value[1].current_revision.observed_at,
                    value[1].item.memory_id,
                ),
                reverse=True,
            )[:lexical_limit]
        )
        lexical_ids = {record.item.memory_id for record in lexical}
        recent_limit = limit - len(lexical)
        recent = tuple(
            record for record in eligible if record.item.memory_id not in lexical_ids
        )[:recent_limit]
        return RecallCandidateSet(
            candidates=tuple(
                MemoryRecallCandidate(
                    item=record.item,
                    current_revision=record.current_revision,
                )
                for record in (*lexical, *recent)
            ),
            lexical_count=len(lexical),
            vector_count=0,
            recent_count=len(recent),
        )

    def find_recall_candidates_by_ids(
        self,
        principal: PrincipalContext,
        *,
        memory_ids: Sequence[UUID],
        effective_at: datetime,
    ) -> tuple[MemoryRecallCandidate, ...]:
        """按 memory_id 集合加载可见的当前活动候选（关系感知召回补漏用）。"""

        requested = frozenset(memory_ids)
        owner_ids = principal.visible_owner_ids
        return tuple(
            MemoryRecallCandidate(
                item=record.item,
                current_revision=record.current_revision,
            )
            for record in self._records.values()
            if record.item.memory_id in requested
            and record.item.owner_id in owner_ids
            and record.current_revision.lifecycle_status is LifecycleStatus.ACTIVE
            and _is_effective(record.current_revision, effective_at)
        )

    def load_recall_evidence(
        self,
        principal: PrincipalContext,
        *,
        revision_ids: Sequence[UUID],
        per_revision_limit: int,
    ) -> dict[UUID, tuple[Evidence, ...]]:
        """返回 selected owned revision 最近的有限来源。"""

        if per_revision_limit < 1:
            raise ValueError("per_revision_limit must be positive")
        requested = frozenset(revision_ids)
        owner_ids = principal.visible_owner_ids
        return {
            record.current_revision.revision_id: record.evidence[-per_revision_limit:]
            for record in self._records.values()
            if record.item.owner_id in owner_ids
            and record.current_revision.revision_id in requested
        }

    def maintain(
        self,
        *,
        effective_at: datetime,
        review_cutoff: datetime,
        limit: int,
    ) -> MaintenanceResult:
        """按与 PostgreSQL 相同的批次配额物化终态。"""

        if limit < 1:
            raise ValueError("limit must be positive")
        memory_limit = (limit + 1) // 2
        review_limit = limit - memory_limit
        memory_targets = tuple(
            sorted(
                (
                    record
                    for record in self._records.values()
                    if record.current_revision.is_current
                    and record.current_revision.lifecycle_status
                    is LifecycleStatus.ACTIVE
                    and record.current_revision.valid_until is not None
                    and record.current_revision.valid_until <= effective_at
                ),
                key=lambda record: (
                    record.current_revision.valid_until,
                    record.current_revision.revision_id,
                ),
            )[:memory_limit]
        )
        expired_keys = {
            (record.item.owner_id, record.item.memory_id) for record in memory_targets
        }
        for record in memory_targets:
            revision = replace(
                record.current_revision,
                lifecycle_status=LifecycleStatus.EXPIRED,
            )
            self._records[record.item.memory_id] = replace(
                record,
                current_revision=revision,
            )
            self._history[record.item.memory_id] = tuple(
                replace(entry, revision=revision)
                if entry.revision.revision_id == revision.revision_id
                else entry
                for entry in self._history[record.item.memory_id]
            )

        stale_relation_count = 0
        relation_contexts: list[ExpiredRelationContext] = []
        with self._relation_lock:
            for relation_id, relation in tuple(self._relations.items()):
                if relation.status is not RelationStatus.ACTIVE:
                    continue
                if relation.created_at > effective_at:
                    continue
                endpoint_keys = {
                    (relation.owner_id, relation.source_memory_id),
                    (relation.owner_id, relation.target_memory_id),
                }
                if not expired_keys.intersection(endpoint_keys):
                    continue
                self._relations[relation_id] = replace(
                    relation,
                    status=RelationStatus.STALE,
                    stale_at=effective_at,
                    stale_reason="endpoint_expired",
                )
                stale_relation_count += 1
                context = self._build_expired_relation_context(
                    relation, expired_keys
                )
                if context is not None:
                    relation_contexts.append(context)

        review_targets = tuple(
            sorted(
                (
                    review
                    for review in self._reviews.values()
                    if review.status is ReviewStatus.PENDING
                    and (
                        (
                            review.candidate.valid_until is not None
                            and review.candidate.valid_until <= effective_at
                        )
                        or review.created_at <= review_cutoff
                    )
                ),
                key=lambda review: (
                    review.candidate.valid_until or review.created_at,
                    review.review_id,
                ),
            )[:review_limit]
        )
        for review in review_targets:
            self._reviews[review.review_id] = replace(
                review,
                status=ReviewStatus.EXPIRED,
                decided_at=effective_at,
            )
        return MaintenanceResult(
            effective_at=effective_at,
            expired_memory_count=len(memory_targets),
            expired_review_count=len(review_targets),
            stale_relation_count=stale_relation_count,
            has_more=(
                len(memory_targets) == memory_limit
                or (review_limit > 0 and len(review_targets) == review_limit)
            ),
            expired_relation_contexts=tuple(relation_contexts),
        )

    def _build_expired_relation_context(
        self,
        relation: MemoryRelation,
        expired_keys: set[tuple[str, UUID]],
    ) -> ExpiredRelationContext | None:
        """为一条失效关系构造双端上下文：过期端为 expired_*，另一端为 focus_*。

        端点记忆须仍存在于 ``_records``（已被置为 expired 但未被移除）；找不到
        任一端点时返回 None（数据不一致，跳过该关系的提醒派生）。
        """

        source_record = self._records.get(relation.source_memory_id)
        target_record = self._records.get(relation.target_memory_id)
        if source_record is None or target_record is None:
            return None
        source_expired = (
            relation.owner_id,
            relation.source_memory_id,
        ) in expired_keys
        if source_expired:
            expired_item = source_record.item
            focus_item = target_record.item
        else:
            expired_item = target_record.item
            focus_item = source_record.item
        return ExpiredRelationContext(
            owner_id=relation.owner_id,
            profile_id=relation.profile_id,
            relation_type=relation.relation_type,
            expired_memory_id=expired_item.memory_id,
            expired_subject=expired_item.subject,
            expired_memory_type=expired_item.memory_type,
            focus_memory_id=focus_item.memory_id,
            focus_subject=focus_item.subject,
            focus_memory_type=focus_item.memory_type,
        )

    def extract_team_common_memories(
        self,
        *,
        team_owner_id: str,
        member_owner_ids: tuple[str, ...],
        profile_id: str,
        effective_at: datetime,
        similarity_threshold: float,
        min_cluster_size: int,
    ) -> TeamExtractionResult:
        """扫描成员个人记忆，用 embedding 余弦相似度聚类并写团队 pending review。

        语义与 PostgreSQL 版本对齐：仅纳入有 embedding 的 active/effective 成员
        记忆；按 memory_type 分组后贪心聚类；簇需满足最小尺寸且至少 2 个不同成员，
        簇内同时出现对立 business_progress（resolved/invalidated）时丢弃（弱方向校验）；
        簇内聚合 subject/content/assertion/sensitivity/validity，confidence 取不同 owner 数 / 成员数；
        同 subject+type 的已有团队 pending 或 confirmed 不重复创建。Run 级幂等：同
        (team, profile, effective_at) 已运行则直接返回既有计数，不重复扫描。
        """

        members = list(member_owner_ids)
        # run 级幂等：同 (team, profile, effective_at) 已运行则返回既有计数。
        run_key = (team_owner_id, profile_id, effective_at)
        existing_run = self._team_extraction_runs.get(run_key)
        if existing_run is not None:
            return existing_run
        if not members:
            result = TeamExtractionResult(
                team_owner_id=team_owner_id,
                member_count=0,
                memory_count=0,
                cluster_count=0,
                candidate_count=0,
                completed_at=effective_at,
            )
            self._team_extraction_runs[run_key] = result
            return result
        eligible: list[dict[str, Any]] = []
        for record in self._records.values():
            if record.item.owner_id not in members:
                continue
            if record.item.profile_id != profile_id:
                continue
            revision = record.current_revision
            if (
                revision.lifecycle_status is not LifecycleStatus.ACTIVE
                or not _is_effective(revision, effective_at)
                or revision.embedding is None
            ):
                continue
            eligible.append(
                {
                    "memory_type": record.item.memory_type,
                    "memory_id": record.item.memory_id,
                    "owner_id": record.item.owner_id,
                    "subject": record.item.subject,
                    "content": revision.content,
                    "embedding": revision.embedding,
                    "assertion_kind": revision.assertion_kind,
                    "observed_at": revision.observed_at,
                    "extraction_confidence": revision.extraction_confidence,
                    "sensitivity_level": revision.sensitivity_level,
                    "valid_from": revision.valid_from,
                    "valid_until": revision.valid_until,
                    "business_progress": revision.business_progress,
                }
            )

        memory_count = len(eligible)
        if memory_count == 0:
            return TeamExtractionResult(
                team_owner_id=team_owner_id,
                member_count=len(members),
                memory_count=0,
                cluster_count=0,
                candidate_count=0,
                completed_at=effective_at,
            )

        # 按 memory_type/owner/memory_id 排序使聚类可复现（与 PostgreSQL ORDER BY 对齐）。
        eligible.sort(
            key=lambda e: (
                str(e["memory_type"]),
                str(e["owner_id"]),
                str(e["memory_id"]),
            ),
        )
        groups: dict[str, list[dict[str, Any]]] = {}
        for entry in eligible:
            groups.setdefault(entry["memory_type"], []).append(entry)
        embedding_clusters: list[list[dict[str, Any]]] = []
        for group in groups.values():
            for cluster in _greedy_cluster(group, similarity_threshold):
                embedding_clusters.append(cluster)
        # 簇需同时满足最小尺寸和至少 2 个不同成员，避免单成员回声室。
        # 弱方向校验：簇内同时出现 resolved/invalidated 对立 business_progress 时丢弃，
        # 避免把立场相反的判断并成同一条团队共性候选。
        valid_embedding_clusters = [
            c
            for c in embedding_clusters
            if len(c) >= min_cluster_size
            and len({m["owner_id"] for m in c}) >= 2
            and not has_conflicting_business_progress(c)
        ]
        cluster_count = len(valid_embedding_clusters)

        candidate_count = 0
        for cluster in valid_embedding_clusters:
            # embedding 簇组内单类型。
            memory_type = cluster[0]["memory_type"]
            # 确定性 subject/content 选择（频次优先 + 字典序兜底），替换非确定性 max(set,...)。
            subject = select_cluster_subject(cluster)
            content = select_cluster_content(cluster)
            unique_owners = len({m["owner_id"] for m in cluster})
            base_rationale = f"团队共性提取：{unique_owners} 个成员写了相似内容"
            save_rationale = format_divergence_rationale(
                cluster,
                base=base_rationale,
                subject=subject,
                content=content,
            )
            # 幂等：同 subject+type 的 pending 或 confirmed 不重复创建。
            # 扩到 confirmed 防止一条共识被确认后、成员继续写同样东西时又产出新 pending。
            # 注：生产 PostgreSQL 版本额外按 embedding 余弦距离做语义去重，
            # in_memory 版本因 Candidate 无 embedding 字段只做精确 subject 匹配。
            already_exists = any(
                review.owner_id == team_owner_id
                and review.candidate.subject == subject
                and review.candidate.memory_type == memory_type
                and review.status
                in (ReviewStatus.PENDING, ReviewStatus.CONFIRMED)
                for review in self._reviews.values()
            )
            if already_exists:
                continue
            confidence = round(unique_owners / len(members), 6)
            confidence = round(unique_owners / len(members), 6)
            candidate = _team_candidate_from_cluster(
                cluster,
                team_owner_id=team_owner_id,
                profile_id=profile_id,
                subject=subject,
                content=content,
                memory_type=memory_type,
                confidence=confidence,
                observed_at=effective_at,
                save_rationale=save_rationale,
            )
            review = ReviewItem(
                review_id=self._id_factory(),
                candidate=candidate,
                status=ReviewStatus.PENDING,
                created_at=effective_at,
            )
            self._validate_review(
                PrincipalContext(team_owner_id),
                review,
            )
            self._reviews[review.review_id] = review
            candidate_count += 1

        result = TeamExtractionResult(
            team_owner_id=team_owner_id,
            member_count=len(members),
            memory_count=memory_count,
            cluster_count=cluster_count,
            candidate_count=candidate_count,
            completed_at=effective_at,
        )
        self._team_extraction_runs[run_key] = result
        return result

    def revoke(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> MemoryRecord | None:
        """把可见的 active revision 标记为 revoked，并物化其 revision-scoped 活动关系为 stale。"""

        record = self.get(principal, memory_id)
        if record is None:
            return None
        revision = record.current_revision
        if revision.lifecycle_status is LifecycleStatus.REVOKED:
            return record
        if revision.lifecycle_status is not LifecycleStatus.ACTIVE:
            return None
        revoked = replace(revision, lifecycle_status=LifecycleStatus.REVOKED)
        updated = replace(record, current_revision=revoked)
        self._records[memory_id] = updated
        self._history[memory_id] = tuple(
            replace(entry, revision=revoked)
            if entry.revision.revision_id == revision.revision_id
            else entry
            for entry in self._history[memory_id]
        )
        # 与 replacement/revoke(PG) 对齐：指向该 memory 的 revision-scoped 活动边物化为 stale。
        # stale_at 用撤销时刻（now），而非 revision.created_at--关系可能在该 revision 之后建立。
        revoked_principal = PrincipalContext(revision.owner_id)
        with self._relation_lock:
            self._relations = _stale_revoked_relations(
                self._relations,
                revoked_principal,
                memory_id,
                stale_at=datetime.now(UTC),
            )
        return updated

    def link_relation(
        self,
        principal: PrincipalContext,
        relation: MemoryRelation,
        *,
        effective_at: datetime,
    ) -> MemoryRelation:
        """显式写入手动关系，端点须在可见 owner 集合内且 active。"""

        if relation.origin is not RelationOrigin.MANUAL:
            raise ValueError("explicit relation write must be manual")
        self._validate_relation_write(
            principal,
            relation,
            records=self._records,
            effective_at=effective_at,
        )
        with self._relation_lock:
            for existing in self._relations.values():
                if (
                    existing.owner_id == principal.owner_id
                    and existing.source_memory_id == relation.source_memory_id
                    and existing.target_memory_id == relation.target_memory_id
                    and existing.relation_type == relation.relation_type
                    and existing.status is RelationStatus.ACTIVE
                ):
                    return existing
            if relation.relation_id in self._relations:
                raise ValueError("relation_id must be unique")
            self._relations[relation.relation_id] = relation
        return relation

    def revoke_relation(
        self,
        principal: PrincipalContext,
        relation_id: UUID,
        *,
        revoked_at: datetime,
    ) -> MemoryRelation | None:
        """把可见的 active relation 标记为 revoked。"""

        with self._relation_lock:
            relation = self._relations.get(relation_id)
            if relation is None or relation.owner_id not in principal.visible_owner_ids:
                return None
            if relation.status is RelationStatus.REVOKED:
                return relation
            revoked = replace(
                relation,
                status=RelationStatus.REVOKED,
                revoked_at=revoked_at,
            )
            self._relations[relation_id] = revoked
            return revoked

    def list_relations(
        self,
        principal: PrincipalContext,
        *,
        memory_ids: Sequence[UUID],
        active_only: bool,
        effective_at: datetime | None = None,
    ) -> Sequence[MemoryRelationSummary]:
        """列出与指定 memory 集合关联的关系摘要（含出入方向）。"""

        requested = frozenset(memory_ids)
        if not requested:
            return ()
        resolved_time = effective_at or datetime.now(UTC)
        owner_ids = principal.visible_owner_ids
        summaries: list[MemoryRelationSummary] = []
        for relation in self._relations.values():
            if relation.owner_id not in owner_ids:
                continue
            if not (
                relation.source_memory_id in requested
                or relation.target_memory_id in requested
            ):
                continue
            source = self.get(principal, relation.source_memory_id)
            target = self.get(principal, relation.target_memory_id)
            if source is None or target is None:
                continue
            if active_only and (
                relation.status is not RelationStatus.ACTIVE
                or any(
                    record.current_revision.lifecycle_status
                    is not LifecycleStatus.ACTIVE
                    or not _is_effective(record.current_revision, resolved_time)
                    for record in (source, target)
                )
            ):
                continue
            if relation.source_memory_id in requested:
                summaries.append(
                    MemoryRelationSummary(
                        relation=relation,
                        direction=RelationDirection.OUTGOING,
                        related_memory_id=target.item.memory_id,
                        related_subject=target.item.subject,
                        related_memory_type=target.item.memory_type,
                    )
                )
            if relation.target_memory_id in requested:
                summaries.append(
                    MemoryRelationSummary(
                        relation=relation,
                        direction=RelationDirection.INCOMING,
                        related_memory_id=source.item.memory_id,
                        related_subject=source.item.subject,
                        related_memory_type=source.item.memory_type,
                    )
                )
        return tuple(
            sorted(
                summaries,
                key=lambda value: (
                    value.relation.created_at,
                    value.relation.relation_id,
                    value.direction.value,
                ),
            )
        )

    def get_history(
        self,
        principal: PrincipalContext,
        memory_id: UUID,
    ) -> Sequence[MemoryHistoryEntry]:
        """按 revision 倒序返回可见记忆的完整历史。"""

        record = self._records.get(memory_id)
        if record is None or record.item.owner_id not in principal.visible_owner_ids:
            return ()
        return tuple(
            sorted(
                self._history[memory_id],
                key=lambda value: value.revision.revision_number,
                reverse=True,
            )
        )

    def get_capture(
        self,
        principal: PrincipalContext,
        *,
        profile_id: str,
        conversation_id: str,
        source_turn_id: str,
        event_id: str | None = None,
    ) -> CaptureResult | None:
        """按 event_id 或 legacy 四元组查询幂等 capture 结果。"""

        entry = self._captures.get(
            self._capture_lookup_key(
                owner_id=principal.owner_id,
                profile_id=profile_id,
                conversation_id=conversation_id,
                source_turn_id=source_turn_id,
                event_id=event_id,
            )
        )
        return entry[0] if entry is not None else None

    def commit_capture(
        self,
        principal: PrincipalContext,
        write: CaptureWrite,
    ) -> CaptureResult:
        """在一个锁内幂等提交 capture 及其全部派生写入。

        语义与 PostgreSQL 版本对齐：已有且非 REPROCESS_REQUIRED/PENDING 的
        capture 直接重放返回；PENDING（入队待抽取）与 REPROCESS_REQUIRED
        走重写路径覆盖终态；replacement 会同步把引用旧 revision 的
        active relation 置为 stale。
        """

        with self._capture_lock:
            return self._commit_capture_locked(principal, write)

    def _commit_capture_locked(
        self,
        principal: PrincipalContext,
        write: CaptureWrite,
    ) -> CaptureResult:
        """在已持锁的前提下执行 capture 写入的校验与物化。"""

        result = write.result
        if result.owner_id != principal.owner_id:
            raise ValueError("capture owner must match trusted principal")
        if result.status is not CaptureStatus.COMPLETED and (
            write.memories
            or write.reviews
            or write.duplicate_evidence
            or write.replacements
            or write.relations
        ):
            raise ValueError("failed capture cannot persist candidate content")
        key = self._capture_key(result)
        existing_entry = self._captures.get(key)
        if existing_entry is not None:
            existing = existing_entry[0]
            if (
                result.payload_fingerprint is not None
                and existing.payload_fingerprint != result.payload_fingerprint
            ):
                raise IdempotencyConflictError(
                    "event identifier was reused with a different payload"
                )
            if existing.status not in (
                CaptureStatus.REPROCESS_REQUIRED,
                CaptureStatus.PENDING,
            ):
                return replace(existing, replayed=True)
            if existing.capture_id != result.capture_id:
                raise ValueError("reprocessed capture must preserve capture_id")

        record_ids = {record.item.memory_id for record in write.memories}
        lifecycle_ids = {
            operation.memory_id
            for operation in (*write.duplicate_evidence, *write.replacements)
        }
        review_ids = {review.review_id for review in write.reviews}
        relation_ids = {relation.relation_id for relation in write.relations}
        if len(record_ids) != len(write.memories):
            raise ValueError("capture contains duplicate memory ids")
        if len(review_ids) != len(write.reviews):
            raise ValueError("capture contains duplicate review ids")
        if len(relation_ids) != len(write.relations):
            raise ValueError("capture contains duplicate relation ids")
        if len(lifecycle_ids) != (
            len(write.duplicate_evidence) + len(write.replacements)
        ):
            raise ValueError("capture contains conflicting lifecycle writes")
        if record_ids & lifecycle_ids:
            raise ValueError("new memories cannot also be lifecycle targets")
        for record in write.memories:
            self._validate_record(principal, record)
            if record.item.memory_id in self._records:
                raise ValueError("memory_id must be unique")
        for review in write.reviews:
            self._validate_review(principal, review)
            if review.review_id in self._reviews:
                raise ValueError("review_id must be unique")
        for outcome in result.outcomes:
            if (
                outcome.memory_id is not None
                and outcome.memory_id not in record_ids | lifecycle_ids
            ):
                raise ValueError("capture outcome references unknown memory")
            if outcome.review_id is not None and outcome.review_id not in review_ids:
                raise ValueError("capture outcome references unknown review")

        records = dict(self._records)
        history = dict(self._history)
        reviews = dict(self._reviews)
        captures = dict(self._captures)
        for record in write.memories:
            records[record.item.memory_id] = record
            history[record.item.memory_id] = (
                MemoryHistoryEntry(
                    revision=record.current_revision,
                    evidence=record.evidence,
                ),
            )
        for duplicate in write.duplicate_evidence:
            current = self._require_lifecycle_target(
                records,
                principal,
                duplicate.memory_id,
                duplicate.expected_revision_id,
            )
            self._validate_new_evidence(
                principal,
                current.current_revision,
                duplicate.evidence,
            )
            updated = replace(
                current,
                evidence=(*current.evidence, duplicate.evidence),
            )
            records[duplicate.memory_id] = updated
            history[duplicate.memory_id] = tuple(
                replace(entry, evidence=updated.evidence)
                if entry.revision.revision_id == duplicate.expected_revision_id
                else entry
                for entry in history[duplicate.memory_id]
            )
        for replacement in write.replacements:
            current = self._require_lifecycle_target(
                records,
                principal,
                replacement.memory_id,
                replacement.expected_revision_id,
            )
            self._validate_replacement(principal, current, replacement)
            superseded = replace(
                current.current_revision,
                lifecycle_status=LifecycleStatus.SUPERSEDED,
                is_current=False,
            )
            new_record = MemoryRecord(
                item=current.item,
                current_revision=replacement.revision,
                evidence=replacement.evidence,
            )
            records[replacement.memory_id] = new_record
            history[replacement.memory_id] = (
                *(
                    replace(entry, revision=superseded)
                    if entry.revision.revision_id == replacement.expected_revision_id
                    else entry
                    for entry in history[replacement.memory_id]
                ),
                MemoryHistoryEntry(
                    revision=replacement.revision,
                    evidence=replacement.evidence,
                ),
            )
        with self._relation_lock:
            relations = dict(self._relations)
            for replacement in write.replacements:
                relations = _stale_revision_relations(
                    relations,
                    principal,
                    replacement,
                )
            for relation in write.relations:
                provenance = relation.provenance
                if (
                    relation.origin is not RelationOrigin.AUTOMATIC
                    or provenance is None
                    or provenance.capture_id != result.capture_id
                    or provenance.conversation_id != result.conversation_id
                    or provenance.source_turn_id != result.source_turn_id
                ):
                    raise ValueError(
                        "capture relation provenance must match capture result"
                    )
                self._validate_relation_write(
                    principal,
                    relation,
                    records=records,
                    effective_at=relation.created_at,
                )
                duplicate = any(
                    existing_relation.owner_id == principal.owner_id
                    and existing_relation.source_memory_id == relation.source_memory_id
                    and existing_relation.target_memory_id == relation.target_memory_id
                    and existing_relation.relation_type == relation.relation_type
                    and existing_relation.status is RelationStatus.ACTIVE
                    for existing_relation in relations.values()
                )
                if duplicate:
                    continue
                if relation.relation_id in relations:
                    raise ValueError("relation_id must be unique")
                relations[relation.relation_id] = relation
            reviews.update((review.review_id, review) for review in write.reviews)
            captures[key] = (result, write.content, write.subject_hint)
            self._records = records
            self._history = history
            self._reviews = reviews
            self._captures = captures
            self._relations = relations
        return result

    def commit_capture_enqueue(
        self,
        principal: PrincipalContext,
        write: CaptureEnqueueWrite,
    ) -> CaptureResult:
        """入队专用：存 PENDING 行（含 content/subject_hint），或对已存在行 replay。"""

        result = write.result
        if result.owner_id != principal.owner_id:
            raise ValueError("capture owner must match trusted principal")
        with self._capture_lock:
            key = self._capture_key(result)
            existing_entry = self._captures.get(key)
            if existing_entry is not None:
                existing = existing_entry[0]
                if (
                    result.payload_fingerprint is not None
                    and existing.payload_fingerprint != result.payload_fingerprint
                ):
                    raise IdempotencyConflictError(
                        "event identifier was reused with a different payload"
                    )
                return replace(existing, replayed=True)
            captures = dict(self._captures)
            captures[key] = (result, write.content, write.subject_hint)
            self._captures = captures
            return result

    def list_pending_captures(
        self,
        *,
        limit: int,
    ) -> tuple[PendingCapture, ...]:
        """捞取所有 PENDING capture（跨 owner，内存版无行锁，单线程测试用）。"""

        if limit < 1:
            raise ValueError("limit must be positive")
        return tuple(
            PendingCapture(
                capture_id=entry[0].capture_id,
                owner_id=entry[0].owner_id,
                profile_id=entry[0].profile_id,
                conversation_id=entry[0].conversation_id,
                source_turn_id=entry[0].source_turn_id,
                content=entry[1],
                subject_hint=entry[2],
                observed_at=entry[0].created_at,
                created_at=entry[0].created_at,
                metadata=entry[0].metadata,
                event_id=entry[0].event_id,
                contract_version=entry[0].contract_version,
                payload_fingerprint=entry[0].payload_fingerprint,
            )
            for entry in self._captures.values()
            if entry[0].status is CaptureStatus.PENDING
        )[:limit]

    def list_reviews(
        self,
        principal: PrincipalContext,
        *,
        status: ReviewStatus,
    ) -> Sequence[ReviewItem]:
        """列出可见 owner 集合内指定状态的待审项。"""

        owner_ids = principal.visible_owner_ids
        return tuple(
            sorted(
                (
                    review
                    for review in self._reviews.values()
                    if review.owner_id in owner_ids and review.status is status
                ),
                key=lambda value: (value.created_at, value.review_id),
            )
        )

    def get_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
    ) -> ReviewItem | None:
        """读取单条可见 review。"""

        review = self._reviews.get(review_id)
        if review is None or review.owner_id not in principal.visible_owner_ids:
            return None
        return review

    def resolve_review(
        self,
        principal: PrincipalContext,
        review_id: UUID,
        *,
        status: ReviewStatus,
        decided_at: datetime,
        memory: MemoryRecord | None = None,
        duplicate_evidence: DuplicateEvidenceWrite | None = None,
        replacement: ReplacementWrite | None = None,
    ) -> ReviewItem | None:
        """完成 review 决议及其派生写入，语义与 PostgreSQL 版本对齐。"""

        review = self.get_review(principal, review_id)
        if review is None:
            return None
        if review.status is status:
            return review
        if review.status is not ReviewStatus.PENDING:
            return None
        if status is ReviewStatus.CONFIRMED:
            writes = tuple(
                value
                for value in (memory, duplicate_evidence, replacement)
                if value is not None
            )
            if len(writes) != 1:
                raise ValueError("confirmed review requires one memory write")
            if memory is not None:
                self._validate_record(principal, memory)
                self._validate_review_memory(review, memory)
                if memory.item.memory_id in self._records:
                    raise ValueError("memory_id must be unique")
        elif status is ReviewStatus.REJECTED:
            if any(
                value is not None for value in (memory, duplicate_evidence, replacement)
            ):
                raise ValueError("rejected review cannot create memory")
        else:
            raise ValueError("review resolution must be confirmed or rejected")

        resolved_memory_id = (
            memory.item.memory_id
            if memory is not None
            else (
                duplicate_evidence.memory_id
                if duplicate_evidence is not None
                else replacement.memory_id
                if replacement is not None
                else None
            )
        )
        resolved = replace(
            review,
            status=status,
            decided_at=decided_at,
            resolved_memory_id=(
                resolved_memory_id if status is ReviewStatus.CONFIRMED else None
            ),
        )
        records = dict(self._records)
        history = dict(self._history)
        reviews = dict(self._reviews)
        if memory is not None:
            records[memory.item.memory_id] = memory
            history[memory.item.memory_id] = (
                MemoryHistoryEntry(
                    revision=memory.current_revision,
                    evidence=memory.evidence,
                ),
            )
        if duplicate_evidence is not None:
            current = self._require_lifecycle_target(
                records,
                principal,
                duplicate_evidence.memory_id,
                duplicate_evidence.expected_revision_id,
            )
            self._validate_new_evidence(
                principal,
                current.current_revision,
                duplicate_evidence.evidence,
            )
            updated = replace(
                current,
                evidence=(*current.evidence, duplicate_evidence.evidence),
            )
            records[duplicate_evidence.memory_id] = updated
            history[duplicate_evidence.memory_id] = tuple(
                replace(entry, evidence=updated.evidence)
                if entry.revision.revision_id == duplicate_evidence.expected_revision_id
                else entry
                for entry in history[duplicate_evidence.memory_id]
            )
        if replacement is not None:
            current = self._require_lifecycle_target(
                records,
                principal,
                replacement.memory_id,
                replacement.expected_revision_id,
            )
            self._validate_replacement(principal, current, replacement)
            superseded = replace(
                current.current_revision,
                lifecycle_status=LifecycleStatus.SUPERSEDED,
                is_current=False,
            )
            records[replacement.memory_id] = MemoryRecord(
                item=current.item,
                current_revision=replacement.revision,
                evidence=replacement.evidence,
            )
            history[replacement.memory_id] = (
                *(
                    replace(entry, revision=superseded)
                    if entry.revision.revision_id == replacement.expected_revision_id
                    else entry
                    for entry in history[replacement.memory_id]
                ),
                MemoryHistoryEntry(
                    revision=replacement.revision,
                    evidence=replacement.evidence,
                ),
            )
        reviews[review_id] = resolved
        with self._relation_lock:
            relations = dict(self._relations)
            if replacement is not None:
                relations = _stale_revision_relations(
                    relations,
                    principal,
                    replacement,
                )
            self._records = records
            self._history = history
            self._reviews = reviews
            self._relations = relations
        return resolved

    @staticmethod
    def _require_lifecycle_target(
        records: dict[UUID, MemoryRecord],
        principal: PrincipalContext,
        memory_id: UUID,
        expected_revision_id: UUID,
    ) -> MemoryRecord:
        """确认 lifecycle 目标存在、归属正确、仍是 active 当前版。"""

        record = records.get(memory_id)
        if (
            record is None
            or record.item.owner_id != principal.owner_id
            or record.current_revision.revision_id != expected_revision_id
            or record.current_revision.lifecycle_status is not LifecycleStatus.ACTIVE
        ):
            raise ValueError("lifecycle target is no longer current")
        return record

    @staticmethod
    def _validate_new_evidence(
        principal: PrincipalContext,
        revision: MemoryRevision,
        evidence: Evidence,
    ) -> None:
        """校验补充 evidence 归属当前 revision。"""

        if (
            evidence.owner_id != principal.owner_id
            or evidence.memory_id != revision.memory_id
            or evidence.revision_id != revision.revision_id
        ):
            raise ValueError("duplicate evidence must match current revision")

    @staticmethod
    def _validate_replacement(
        principal: PrincipalContext,
        current: MemoryRecord,
        replacement,
    ) -> None:
        """校验 replacement revision 是 current 的后继且 evidence 一致。"""

        revision = replacement.revision
        if (
            revision.owner_id != principal.owner_id
            or revision.memory_id != current.item.memory_id
            or revision.revision_number != current.current_revision.revision_number + 1
            or not revision.is_current
            or revision.lifecycle_status is not LifecycleStatus.ACTIVE
            or not replacement.evidence
        ):
            raise ValueError("replacement revision is invalid")
        for source in replacement.evidence:
            InMemoryMemoryRepository._validate_new_evidence(
                principal,
                revision,
                source,
            )

    def _validate_relation_write(
        self,
        principal: PrincipalContext,
        relation: MemoryRelation,
        *,
        records: dict[UUID, MemoryRecord],
        effective_at: datetime,
    ) -> None:
        """校验关系端点可见、同 profile、符合 policy 且 active 生效。"""

        if relation.owner_id not in principal.visible_owner_ids:
            raise ValueError("relation owner must match trusted principal or team")
        if relation.status is not RelationStatus.ACTIVE:
            raise ValueError("new relation must be active")
        if relation.origin is RelationOrigin.LEGACY:
            raise ValueError("new relation cannot use legacy origin")
        source = records.get(relation.source_memory_id)
        target = records.get(relation.target_memory_id)
        owner_ids = principal.visible_owner_ids
        if (
            source is None
            or target is None
            or source.item.owner_id not in owner_ids
            or target.item.owner_id not in owner_ids
        ):
            raise ValueError("relation endpoints are unavailable")
        if (
            source.item.profile_id != relation.profile_id
            or target.item.profile_id != relation.profile_id
        ):
            raise ValueError("relation endpoints must share the relation profile")
        policy = self._profile_relation_policies.get(
            relation.profile_id,
            {},
        ).get(relation.relation_type)
        if (
            policy is None
            or source.item.memory_type not in policy.source_memory_types
            or target.item.memory_type not in policy.target_memory_types
        ):
            raise ValueError("relation does not match the registered policy")
        for record in (source, target):
            revision = record.current_revision
            if (
                revision.lifecycle_status is not LifecycleStatus.ACTIVE
                or not _is_effective(revision, effective_at)
            ):
                raise ValueError("relation endpoints must be active and effective")
        if (
            relation.source_revision_id != source.current_revision.revision_id
            or relation.target_revision_id != target.current_revision.revision_id
        ):
            raise ValueError("relation revision snapshots must match current endpoints")

    def _validate_record(
        self,
        principal: PrincipalContext,
        record: MemoryRecord,
    ) -> None:
        """校验 record 归属可见 owner 且 memory_type 已注册。"""

        # 允许写入个人 owner 或 principal 所属团队的 owner（团队提升路径）。
        if record.item.owner_id not in principal.visible_owner_ids:
            raise ValueError("record owner must match trusted principal or team")
        profile_types = self._profile_types.get(record.item.profile_id)
        if profile_types is None:
            raise ProfileNotRegisteredError(
                f"profile_id is not registered: {record.item.profile_id}"
            )
        if record.item.memory_type not in profile_types:
            raise InvalidMemoryTypeError(
                "memory type is not registered for profile_id "
                f"{record.item.profile_id}: {record.item.memory_type}"
            )

    def _validate_review(
        self,
        principal: PrincipalContext,
        review: ReviewItem,
    ) -> None:
        """校验 review 归属 principal 且 memory_type 已注册。"""

        if review.owner_id != principal.owner_id:
            raise ValueError("review owner must match trusted principal")
        if review.status is not ReviewStatus.PENDING:
            raise ValueError("new review must be pending")
        profile_types = self._profile_types.get(review.candidate.profile_id)
        if profile_types is None or review.candidate.memory_type not in profile_types:
            raise InvalidMemoryTypeError(
                "review memory type is not registered for profile_id "
                f"{review.candidate.profile_id}: {review.candidate.memory_type}"
            )

    @staticmethod
    def _capture_key(
        result: CaptureResult,
    ) -> tuple[str, ...]:
        """从 CaptureResult 提取幂等查找键。"""

        return InMemoryMemoryRepository._capture_lookup_key(
            owner_id=result.owner_id,
            profile_id=result.profile_id,
            conversation_id=result.conversation_id,
            source_turn_id=result.source_turn_id,
            event_id=result.event_id,
        )

    @staticmethod
    def _capture_lookup_key(
        *,
        owner_id: str,
        profile_id: str,
        conversation_id: str,
        source_turn_id: str,
        event_id: str | None,
    ) -> tuple[str, ...]:
        """构造幂等键：有 event_id 用三元组，否则用 legacy 四元组。"""

        if event_id is not None:
            return (
                owner_id,
                "event",
                event_id,
            )
        return (
            owner_id,
            profile_id,
            "legacy",
            conversation_id,
            source_turn_id,
        )

    @staticmethod
    def _validate_review_memory(
        review: ReviewItem,
        memory: MemoryRecord,
    ) -> None:
        """校验确认 memory 的内容、evidence 与待审 candidate 完全一致。"""

        candidate = review.candidate
        revision = memory.current_revision
        source = memory.evidence[0]
        # owner_id 允许不同：团队提升时 memory 写入团队 owner，
        # candidate 仍是个人 owner。其他字段必须一致。
        if (
            memory.item.profile_id != candidate.profile_id
            or memory.item.subject != candidate.subject
            or memory.item.memory_type != candidate.memory_type
            or revision.content != candidate.content
            or revision.assertion_kind is not candidate.assertion_kind
            or revision.business_progress != candidate.business_progress
            or revision.save_rationale != candidate.save_rationale
            or revision.observed_at != candidate.observed_at
            or revision.extraction_confidence != candidate.confidence
            or revision.verification_status.value != "user_confirmed"
            or revision.sensitivity_level is not candidate.sensitivity_level
            or revision.valid_from != candidate.valid_from
            or revision.valid_until != candidate.valid_until
            or revision.original_time_expression != candidate.original_time_expression
            or revision.normalized_time != candidate.normalized_time
            or source.conversation_id != candidate.conversation_id
            or source.source_turn_id != candidate.source_turn_id
            or source.source_expression != candidate.source_expression
            or source.source_role is not candidate.source_role
            or source.source_message_id != candidate.source_message_id
            or source.source_tool_name != candidate.source_tool_name
            or source.source_type is not candidate.source_type
            or _evidence_document_mismatch(source.document, candidate)
        ):
            raise ValueError("confirmed memory must match pending candidate")


def _evidence_document_mismatch(
    document: EvidenceDocument | None,
    candidate: Candidate,
) -> bool:
    """比较 evidence 的 document 子对象与 candidate 的内联文档字段。"""

    if document is None:
        return any(
            getattr(candidate, field) is not None
            for field in (
                "source_uri",
                "source_title",
                "source_publisher",
                "published_at",
                "retrieved_at",
                "content_hash",
                "citation_locator",
            )
        )
    return (
        document.source_uri != candidate.source_uri
        or document.source_title != candidate.source_title
        or document.source_publisher != candidate.source_publisher
        or document.published_at != candidate.published_at
        or document.retrieved_at != candidate.retrieved_at
        or document.content_hash != candidate.content_hash
        or document.citation_locator != candidate.citation_locator
    )


def _is_effective(revision: MemoryRevision, at_time: datetime) -> bool:
    """revision 在指定时刻是否已生效且未过期。"""

    return revision.valid_from <= at_time and (
        revision.valid_until is None or revision.valid_until > at_time
    )


def _trigram_similarity(left: str, right: str) -> float:
    """用于 InMemory 契约测试的稳定 trigram 近似。"""

    left_trigrams = _trigrams(normalize_memory_text(left))
    right_trigrams = _trigrams(normalize_memory_text(right))
    if not left_trigrams or not right_trigrams:
        return 0.0
    return len(left_trigrams & right_trigrams) / max(
        len(left_trigrams),
        len(right_trigrams),
    )


def _trigrams(value: str) -> frozenset[str]:
    """去空格后取 3-gram 集合，短于 3 字符的退化为整体。"""

    compact = value.replace(" ", "")
    if len(compact) < 3:
        return frozenset({compact}) if compact else frozenset()
    return frozenset(compact[index : index + 3] for index in range(len(compact) - 2))


def _stale_revision_relations(
    relations: dict[UUID, MemoryRelation],
    principal: PrincipalContext,
    replacement: ReplacementWrite,
) -> dict[UUID, MemoryRelation]:
    """返回 replacement 后的关系副本，引用旧 revision 的 active 边置为 stale。"""

    stale_at = replacement.revision.created_at
    current_revision_id = replacement.revision.revision_id
    return {
        relation_id: (
            replace(
                relation,
                status=RelationStatus.STALE,
                stale_at=stale_at,
                stale_reason="endpoint_revision_changed",
            )
            if relation.owner_id in principal.visible_owner_ids
            and relation.scope is RelationScope.REVISION
            and relation.status is RelationStatus.ACTIVE
            and (
                (
                    relation.source_memory_id == replacement.memory_id
                    and relation.source_revision_id != current_revision_id
                )
                or (
                    relation.target_memory_id == replacement.memory_id
                    and relation.target_revision_id != current_revision_id
                )
            )
            else relation
        )
        for relation_id, relation in relations.items()
    }


def _stale_revoked_relations(
    relations: dict[UUID, MemoryRelation],
    principal: PrincipalContext,
    memory_id: UUID,
    *,
    stale_at: datetime,
) -> dict[UUID, MemoryRelation]:
    """返回 revoke 后的关系副本，指向该 memory 的 revision-scoped 活动边置为 stale。"""

    return {
        relation_id: (
            replace(
                relation,
                status=RelationStatus.STALE,
                stale_at=stale_at,
                stale_reason="endpoint_revoked",
            )
            if relation.owner_id in principal.visible_owner_ids
            and relation.scope is RelationScope.REVISION
            and relation.status is RelationStatus.ACTIVE
            and (
                relation.source_memory_id == memory_id
                or relation.target_memory_id == memory_id
            )
            else relation
        )
        for relation_id, relation in relations.items()
    }


def _greedy_cluster(
    memories: list[dict[str, Any]],
    threshold: float,
) -> list[list[dict[str, Any]]]:
    """按 embedding 余弦相似度贪心归簇，语义与 PostgreSQL 版本一致。"""

    assigned = [False] * len(memories)
    clusters: list[list[dict[str, Any]]] = []
    for index, memory in enumerate(memories):
        if assigned[index]:
            continue
        cluster = [memory]
        assigned[index] = True
        for other in range(index + 1, len(memories)):
            if assigned[other]:
                continue
            similarity = _cosine_similarity(
                memory["embedding"],
                memories[other]["embedding"],
            )
            if similarity >= threshold:
                cluster.append(memories[other])
                assigned[other] = True
        clusters.append(cluster)
    return clusters


def _cosine_similarity(
    left: tuple[float, ...] | None,
    right: tuple[float, ...] | None,
) -> float:
    """计算两个 embedding 的余弦相似度；缺失或零向量返回 0。"""

    vector_left = _parse_embedding(left)
    vector_right = _parse_embedding(right)
    if not vector_left or not vector_right:
        return 0.0
    dot = sum(x * y for x, y in zip(vector_left, vector_right, strict=False))
    norm_left = sum(x * x for x in vector_left) ** 0.5
    norm_right = sum(x * x for x in vector_right) ** 0.5
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)


def _parse_embedding(value: tuple[float, ...] | None) -> list[float]:
    """把内存中的 embedding 元组转为 float 列表。"""

    if value is None:
        return []
    return [float(component) for component in value]


def _team_candidate_from_cluster(
    cluster: list[dict[str, Any]],
    *,
    team_owner_id: str,
    profile_id: str,
    subject: str,
    content: str,
    memory_type: str,
    confidence: float,
    observed_at: datetime,
    save_rationale: str,
) -> Candidate:
    """从聚类结果聚合出团队 pending 候选，字段选择与 PostgreSQL 版本对齐。"""

    assertion_kind = _cluster_mode(
        cluster,
        "assertion_kind",
        AssertionKind,
    )
    sensitivity_level = _cluster_mode(
        cluster,
        "sensitivity_level",
        SensitivityLevel,
    )
    valid_from = min(m["valid_from"] for m in cluster)
    valid_until: datetime | None = None
    for member in cluster:
        member_until = member["valid_until"]
        if member_until is not None and (
            valid_until is None or member_until < valid_until
        ):
            valid_until = member_until
    return Candidate(
        candidate_id=uuid4(),
        owner_id=team_owner_id,
        profile_id=profile_id,
        subject=subject,
        memory_type=memory_type,
        content=content,
        assertion_kind=assertion_kind,
        conversation_id="team-extraction",
        source_turn_id="team-extraction",
        source_expression=content,
        save_rationale=save_rationale,
        confidence=confidence,
        durability=CandidateDurability.DURABLE,
        expression_basis=ExpressionBasis.EXPLICIT,
        observed_at=observed_at,
        created_at=observed_at,
        verification_status=VerificationStatus.USER_ASSERTED,
        sensitivity_level=sensitivity_level,
        valid_from=valid_from,
        valid_until=valid_until,
        source_type=EvidenceSourceType.CONVERSATION,
    )


def _cluster_mode(
    cluster: list[dict[str, Any]],
    field: str,
    enum: type,
) -> object:
    """取簇内某枚举字段的众数（频次优先 + 字典序兜底，跨进程可复现）。

    替换原 ``max(set(...), key=count)`` 的非确定性平局兜底。平局时按值的字符串
    表示字典序升序取最小，保证不同进程/Python 版本下结果一致。
    """

    values = [member[field] for member in cluster]
    if not values:
        return None
    counts: dict[object, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return min(counts, key=lambda v: (-counts[v], str(v)))


def _cluster_mode_str(cluster: list[dict[str, Any]], field: str) -> str:
    """取簇内某字符串字段的众数（频次优先 + 字典序兜底），跨进程可复现。"""

    values = [str(member[field]) for member in cluster]
    if not values:
        return ""
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return min(counts, key=lambda v: (-counts[v], v))
