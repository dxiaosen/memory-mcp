## Why

2026-08-07 真实联调日志暴露可观测性短板，影响召回问题的快速定位与性能优化：

1. **召回流程中间环节不可见**：`memory.recall.candidates` 事件已存在但仅在 DEBUG 级别
   打印，默认 INFO 级别看不到召回流程的"候选已召回但被阈值过滤"阶段，无法区分"Repository
   没召回"还是"召回了但被 threshold 过滤"（recommend.md §7）。
2. **召回分阶段耗时缺失**：只能看到 `recall total ≈ 3.7s`，但不知道慢在 embedding、DB、
   排序、evidence 渲染哪个阶段（recommend.md §9）。当前真实环境含远程 DeepSeek、远程
   Aliyun embedding、PostgreSQL、公网 HTTP，不拆阶段耗时无法精确优化。

## What Changes

- **`memory.recall.candidates` 提升为 INFO**：使召回流程 started → candidates → ranked →
  output → completed 在默认日志级别完整可见。
- **召回分阶段耗时**：`memory.recall.completed` 新增 `query_embedding_duration_ms` /
  `repository_candidate_duration_ms` / `ranking_duration_ms` /
  `evidence_loading_duration_ms` / `render_duration_ms`，未执行阶段记 0。
- 同步 logging.md 事件表与单测。

## Capabilities

### New Capabilities

- `memory-observability`：规定召回流程中间事件级别与分阶段耗时字段。

### Modified Capabilities

无。本变更以独立增量能力描述新增行为。

## Impact

- Core：`recall_service.py` `_traced_result` 增 5 个阶段耗时形参并在各 early-return 与最终
  路径透传；`recall.candidates` 事件从 DEBUG 提升为 INFO。
- 文档：logging.md 更新 `recall.candidates` 级别说明与 `recall.completed` 字段表。
- 测试：logging_events 增阶段耗时断言。
- 不改 DB schema、不改 Core 自包含不变量。

## Deferred (recommend.md §8/§10/§11)

以下 P2 项本轮不实现，记录为后续候选：
- §8 跨层请求/重试关联（`transport_request_ref` + attempt 维度）。
- §10 持久化内容日志按对象拆条（summary/memory/review/relation 分行）。
- §11 Pending `reason_flags` 多维诊断（不改 CaptureOutcome 契约，只在内容日志增 flags）。
