# 任务

## 1. 回退主题级归并

- [x] 移除 `MemoryProfile.team_extraction_topic_groups` 属性、`_validate_optional_team_extraction_topic_groups`、`_team_extraction_topic_groups_payload` 及指纹 payload 键
  - 证据：`server/src/memory_mcp/core/ports/profiles.py` 无残留 `team_extraction_topic_groups` 引用
- [x] 移除投研 Profile 的 `business_quality` 组
  - 证据：`server/src/memory_mcp/profiles/investment_research.py` 无 `team_extraction_topic_groups` 字段
- [x] 删除 `team_extraction_helpers.py` 的 `topic_signature`/`topic_jaccard`；保留 `average_embedding`
  - 证据：`server/src/memory_mcp/core/domain/team_extraction_helpers.py` 仅余 `select_cluster_*`/`format_divergence_rationale`/`average_embedding`/`has_conflicting_business_progress`
- [x] PG repository 移除两遍归并、`_resolve_topic_groups`/`_topic_cluster_*`/`_cluster_topic_group_name`/`_cluster_top_keyword`/`_TOPIC_JACCARD_THRESHOLD`
  - 证据：`grep -n "_topic_cluster\|_resolve_topic_groups\|_TOPIC_JACCARD" server/src/memory_mcp/core/adapters/postgresql/repository.py` 无命中
- [x] in_memory 移除对应主题辅助与 `_profile_topic_groups` 字段
  - 证据：`grep -n "_in_memory_topic\|_profile_topic_groups\|_TOPIC_JACCARD" server/src/memory_mcp/core/adapters/in_memory.py` 无命中
- [x] fakes 移除 `TestMemoryProfile.team_extraction_topic_groups`
  - 证据：`tests/support/fakes.py` 无该字段
- [x] 删除 3 个主题集成测试与 2 个主题 helpers 单元测试
  - 证据：`grep -n "topic_group\|team:topic" tests/integration/test_team_extraction.py tests/unit/test_team_extraction_helpers.py` 无命中

## 2. 候选级幂等扩到 confirmed

- [x] PG 候选级幂等查询 `status='pending'` → `status IN ('pending','confirmed')`
  - 证据：`server/src/memory_mcp/core/adapters/postgresql/repository.py` 候选写入前查询
- [x] in_memory 候选级幂等 `ReviewStatus.PENDING` → `in (PENDING, CONFIRMED)`
  - 证据：同位置 `already_exists` 判定
- [x] 新增 `test_idempotent_after_confirmed_no_duplicate_pending` 集成测试
  - 证据：`tests/integration/test_team_extraction.py` 测试通过

## 3. 候选 embedding 取簇中心

- [x] PG embedding 簇候选 embedding 从 `cluster[0]["embedding"]` 改为 `average_embedding([m["embedding"] for m in cluster])`
  - 证据：`server/src/memory_mcp/core/adapters/postgresql/repository.py` 主循环
- [x] 更新 `_embedding_param` docstring
  - 证据：同文件
- [x] in_memory Candidate 无 embedding 字段，不涉及；注释对齐
  - 证据：`server/src/memory_mcp/core/adapters/in_memory.py` 幂等注释更新

## 4. 弱方向校验

- [x] 新增 `has_conflicting_business_progress(cluster)` 纯函数，检测 resolved/invalidated 同存
  - 证据：`server/src/memory_mcp/core/domain/team_extraction_helpers.py`
- [x] 导出至 `core/domain/__init__.py` 与 `core/__init__.py`
  - 证据：两文件 `__all__` 与 import 均含 `has_conflicting_business_progress`
- [x] PG SELECT 加 `r.business_progress`，rows dict 加字段，簇门槛后调用 `has_conflicting_business_progress` 丢弃
  - 证据：`server/src/memory_mcp/core/adapters/postgresql/repository.py`
- [x] in_memory eligible dict 加 `business_progress`，簇门槛后调用丢弃
  - 证据：`server/src/memory_mcp/core/adapters/in_memory.py`
- [x] `_member_record` 测试 helper 支持 `business_progress` 参数
  - 证据：`tests/integration/test_team_extraction.py`
- [x] 新增 3 个 helpers 单元测试 + 2 个集成测试（对立丢弃/同侧正常）
  - 证据：测试通过

## 5. 文档与 OpenSpec

- [x] `docs/design.md` §5.5 更新（删主题归并行、加簇门槛/簇中心/confirmed 幂等/弱方向校验）
  - 证据：`docs/design.md` §5.5
- [x] `docs/config.md` §3.6 删主题归并段、加 confirmed 幂等说明
  - 证据：`docs/config.md` §3.6
- [x] 新建 `openspec/changes/revise-team-extraction-strategy/`（proposal/specs/design/tasks）
  - 证据：`openspec-cn validate revise-team-extraction-strategy --strict` 通过

## 6. 指纹与验证

- [x] 重算 general-work 与 investment-research 指纹，更新 `_BUILT_IN_POLICY_FINGERPRINTS`
  - 证据：指纹测试通过
- [x] `uv run ruff check .` 通过
- [x] `uv run pytest -q` 全套通过（预期 ~341 passed, 13 skipped）
- [x] `uv run pytest tests/contract/test_dependency_boundaries.py` Core 导入铁律通过
- [x] `uv run python -m evals.runner --mode deterministic` CI 门禁通过
