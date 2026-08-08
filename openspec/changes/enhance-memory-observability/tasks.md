## 1. OpenSpec 变更提案

- [x] 1.1 创建 `openspec/changes/enhance-memory-observability/` 及 spec delta。
- [x] 1.2 `openspec-cn validate enhance-memory-observability --strict` 通过。

## 2. recall.candidates 提升为 INFO

- [x] 2.1 `server/src/memory_mcp/core/application/recall_service.py:160`：`memory.recall.candidates` 事件级别从 DEBUG 改为 INFO。
- [x] 2.2 `docs/logging.md`：更新 `memory.recall.candidates` 级别与说明。

## 3. 召回分阶段耗时

- [x] 3.1 `recall_service.py`：在 embedding 前后、`find_recall_candidates` 前后、ranking 前后、evidence_loading 前后、render 前后用 `perf_counter()` 记时戳。
- [x] 3.2 `recall_service.py` `_traced_result`：新增 `query_embedding_duration_ms` / `repository_candidate_duration_ms` / `ranking_duration_ms` / `evidence_loading_duration_ms` / `render_duration_ms` 形参（默认 0），在 `memory.recall.completed` 事件中 `round(…, 3)` 输出。
- [x] 3.3 各 early-return 路径（无 relevant / header 超 budget / 无 selected）与最终路径透传已执行阶段耗时。
- [x] 3.4 `docs/logging.md`：更新 `memory.recall.completed` 字段表。
- [x] 3.5 `tests/unit/test_logging_events.py`：`test_recall_completed_logs_aggregate_counts_and_duration` 增 5 个阶段耗时断言（未执行阶段记 0）。

## 4. 回归与验收

- [x] 4.1 `uv run ruff check server/ tests/` 通过。
- [x] 4.2 `uv run pytest tests/unit tests/contract tests/integration -q` 通过（260 passed, 13 skipped）。
- [ ] 4.3 真实联调：确认 INFO 级别日志含 `recall.candidates` 与 completed 阶段耗时（人工验收，需真实 embedding+PostgreSQL 环境）。

## 5. 延后项（记录为后续候选，本轮不实现）

- [ ] 5.1 §8 `transport_request_ref` 跨层请求/重试关联。
- [ ] 5.2 §10 `memory.capture.persisted` 内容日志按对象拆条。
- [ ] 5.3 §11 Pending `reason_flags` 多维诊断（不改 CaptureOutcome 契约）。
