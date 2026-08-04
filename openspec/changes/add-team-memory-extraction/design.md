## Context

团队记忆当前只能人工显式提升。投研团队多人写了相似内容，需要自动发现共性。embedding 列在向量召回变更中已存在（依赖）。pgvector 已可用。maintenance runner 已有定时任务基础设施。

约束：不做自动确认（进 pending 等人工）；不跨团队；不实时（定时批量）；复用 maintenance runner；core 不依赖 HTTP/DB。

## Goals / Non-Goals

**Goals:**

- 定时扫描团队个人记忆，embedding 聚类发现共性。
- 共性候选写入团队 pending review。
- 幂等（不重复提取同一共性）。
- 复用维护 runner 托管。

**Non-Goals:**

- 不做自动确认（候选进 pending）。
- 不做跨团队提取。
- 不做实时提取。
- 不做聚类算法调优（用固定阈值）。

## Decisions

### 1. 聚类算法

对每个团队（从 Token 配置的 team_ids 派生 team_owner_id）：

```text
1. 查成员：SELECT DISTINCT owner_id FROM memory_items WHERE owner_id LIKE 'tenant:team:%' 之外的个人 owner
   实际是：查该团队所有成员的个人记忆
2. 查个人记忆：SELECT embedding, content, subject, memory_type FROM memory_revisions
   WHERE owner_id IN (成员列表) AND embedding IS NOT NULL AND is_current AND active
3. 按 memory_type 分组
4. 组内两两计算 embedding 余弦相似度
5. 相似度 >= 0.85 的记忆归为一个簇（贪心聚类，不追求最优）
6. 簇大小 >= 2 → 共性候选
7. 对每个候选簇：
   - subject = 簇内最频繁的 subject
   - content = 簇内最长的 content
   - confidence = cluster_size / member_count
   - assertion_kind = 簇内最常见的
   - owner_id = team_owner_id
   - status = pending
```

### 2. Repository 端口新增

```python
class MemoryRepository(Protocol):
    ...

    def find_team_candidates(
        self,
        team_owner_id: str,
        *,
        member_owner_ids: tuple[str, ...],
        profile_id: str,
        effective_at: datetime,
    ) -> tuple[TeamMemoryCandidate, ...]:
        """查团队所有成员的个人 active 记忆（含 embedding），用于聚类。"""
        ...

    def create_team_review(
        self,
        team_owner_id: str,
        candidate: TeamMemoryCandidate,
    ) -> ReviewItem:
        """创建团队 pending review（共性提取出的候选）。"""
        ...
```

### 3. TeamExtractionService

```python
class TeamExtractionService:
    def __init__(self, repository, profile_registry, *, clock, embedding_provider,
                 similarity_threshold=0.85, min_cluster_size=2):
        ...
    
    def run_once(self) -> TeamExtractionResult:
        for team_owner_id in self._team_owner_ids:
            members = self._find_team_members(team_owner_id)
            memories = self._repository.find_team_candidates(
                team_owner_id, member_owner_ids=members, ...
            )
            clusters = self._cluster(memories)
            for cluster in clusters:
                if len(cluster) >= self._min_cluster_size:
                    self._repository.create_team_review(team_owner_id, ...)
```

### 4. 幂等

```sql
-- 每次提取记录到 memory_team_extraction_runs
INSERT INTO memory_team_extraction_runs (run_id, team_owner_id, ...)
VALUES (uuid, team_owner, ...)

-- 写 review 前检查是否已有同 subject + type 的 pending
INSERT INTO memory_review_items (...)
SELECT ... WHERE NOT EXISTS (
    SELECT 1 FROM memory_review_items
    WHERE owner_id = team_owner_id
    AND subject = candidate_subject
    AND memory_type = candidate_memory_type
    AND status = 'pending'
)
```

### 5. 集成到 maintenance runner

```python
# app.py
run_maintenance = (memory_service.run_maintenance,)
team_extraction = (team_extraction_service.run_once,)
team_extraction_interval_seconds = (settings.team_extraction_interval_seconds,)
```

第二个 asyncio task，独立间隔（默认 3600 秒），同样的退避和健康监控。

### 6. 配置

```python
# settings.py
team_extraction_interval_seconds: int = 3600
team_extraction_min_cluster_size: int = 2
team_extraction_similarity_threshold: float = 0.85
```

### 7. 数据流

```text
maintenance runner（每小时）
  → TeamExtractionService.run_once()
    → 对每个 team_owner_id：
      1. 查团队成员的个人 active 记忆（含 embedding）
      2. 按 memory_type 分组
      3. 组内 embedding 聚类（余弦相似度 >= 0.85）
      4. 簇 >= 2 → 共性候选
      5. 写入 memory_review_items（owner=团队，status=pending）
      6. 记录提取运行
    → 团队成员 list_pending_reviews 看到 → confirm 后变团队记忆
```

### 8. 可观测

```text
log: event="memory.team_extraction.completed"
     team_owner_id=<hash>
     member_count=5
     memory_count=23
     cluster_count=3
     candidate_count=2

health: /health 新增 team_extraction 子状态
    "state": "ok|degraded|disabled"
    "last_run_at": "..."
```

## Dependency Direction

- `core.application.TeamExtractionService` 依赖 `core.ports.MemoryRepository` 和 `core.ports.EmbeddingProvider`，不依赖 HTTP/DB。
- `core.adapters.postgresql` 实现 `find_team_candidates` 和 `create_team_review`。
- `app.py` 集成第二个 runner。
- 依赖 `add-vector-recall` 的 embedding 列。

## Risks / Trade-offs

- [聚类精度] → 固定阈值 0.85，不做算法调优；候选进 pending 等人工确认，不自动写入。
- [提取延迟] → 每小时一次批量，不做实时。
- [重复提取] → 幂等检查：已有同 subject+type 的 pending 不重复创建。
- [无 embedding 的记忆不参与] → 依赖向量召回变更已让新记忆有 embedding。
