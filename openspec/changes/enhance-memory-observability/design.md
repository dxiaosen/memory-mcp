# 设计决策

## D1：recall.candidates 从 DEBUG 提升为 INFO，不新增事件

**决策**：`memory.recall.candidates` 事件已存在（`recall_service.py:160`），级别从
DEBUG 提升为 INFO，字段不变。

**理由**：recommend.md §7 误以为该事件不存在，实际是级别问题。提升为 INFO 使召回流程
started → candidates → ranked → output → completed 在默认级别完整可见，无需新增事件，
改动最小。`recall.candidates` 记录 `candidate_count`/`lexical_count`/`vector_count`/
`recent_count`，直接回答"是 Repository 没召回还是召回了但被阈值过滤"。

## D2：阶段耗时走 completed 事件字段，不新增阶段事件

**决策**：在 `memory.recall.completed` 事件新增 5 个 `*_duration_ms` 字段
（`query_embedding_duration_ms` / `repository_candidate_duration_ms` /
`ranking_duration_ms` / `evidence_loading_duration_ms` / `render_duration_ms`），
未执行阶段记 0。不新增阶段事件。

**理由**：recommend.md §9.3 建议至少统计 5 个阶段耗时。放在 `completed` 聚合事件里
（而非每阶段单独事件）避免日志膨胀——一次召回一条 completed 即可看到全阶段耗时
分布。`_traced_result` 是所有 early-return 与最终路径的唯一出口，在这里统一记录，
未到达的阶段默认 0（如零结果路径不执行 evidence_loading/render）。

## D3：§8/§10/§11 本轮延后

**决策**：`transport_request_ref`（§8）、persisted 内容日志按对象拆条（§10）、
Pending `reason_flags` 多维诊断（§11）本轮不实现。

**理由**：
- §8 需在 Agent 与 Server 两侧贯通 `transport_request_ref`，跨包改动面大且需协调
  MCP transport 层 request_id 语义。
- §10 需重构 `memory.capture.persisted` 内容事件为多条子事件，影响内容日志消费者。
- §11 需在准入策略层重算所有触发原因（非仅 primary），改动 admission 决策路径。
三者均为开发期诊断增强，当前 §7+§9 已能解决主要定位需求，优先级低于 P0/P1 的
正确性与语义修复。
