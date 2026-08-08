## 1. OpenSpec 变更提案

- [x] 1.1 创建 `openspec/changes/fix-capture-observability-counts/` 及 spec delta。
- [x] 1.2 `openspec-cn validate fix-capture-observability-counts --strict` 通过。

## 2. 计数语义 + 被拒候选可调试（§1）

- [x] 2.1 `candidate_processing.py`：新增 `RejectedProposal` dataclass；`CandidateProcessingResult` 增 `rejected_proposals` + `timing` 字段。
- [x] 2.2 `process` 两处 discard（invalid_source_expression / ambiguous_source_message）构造 `RejectedProposal` 加入 rejected 列表。
- [x] 2.3 `capture_service.py`：`memory.capture.completed` 新增 `extracted_candidate_count`（len(proposals)）、`outcome_count`（len(outcomes)）。
- [x] 2.4 `capture_service.py`：新增 `memory.capture.validation` 内容事件，输出 extracted/validated count + rejected proposals 完整字段。
- [x] 2.5 `tests/integration/test_capture_service.py::test_capture_counts_reconcile_across_stages`：断言 outcome = auto_save + pending + discarded + blocked。

## 3. 候选原子化 + Evidence 覆盖（§2）

- [x] 3.1 `extraction/backends.py` `_system_prompt`：增"一候选一事实/一推断，混合拆分；source_expression 必须完整支撑 content，跨来源拆多候选"。

## 4. assertion_kind 与 expression_basis 一致（§3）

- [x] 4.1 `candidate_processing.py` `_normalize_assertion_kind`：增 `expression_basis` 参数；document/tool/web + inferred -> system_inference；+ explicit + 非外部 -> external_fact；+ ambiguous 不改。
- [x] 4.2 调用处传入 `proposal.expression_basis`；`assertion_normalized` DEBUG 事件增 `expression_basis` 字段。
- [x] 4.3 `extraction/backends.py` `_system_prompt`：补"external_fact 配 explicit，system_inference 配 inferred"。
- [x] 4.4 `tests/integration/test_capture_service.py::test_document_inferred_assertion_kind_normalized_to_system_inference`：断言 document+inferred+external_fact -> system_inference。
- [x] 4.5 `tests/unit/test_logging_events.py`：assertion_normalized 事件断言含 expression_basis。

## 5. source_uri workspace-relative（§4）

- [x] 5.1 `agent/transcript.py` `extract_document_messages`：增 `cwd` 参数；新增 `_workspace_relative(file_path, cwd)` 用 `os.path.relpath` 转换，分隔符统一 `/`，无 cwd 保留原路径。
- [x] 5.2 `agent/hosts.py`：调用处传 `cwd=event.cwd`。
- [x] 5.3 `tests/integration/test_agent_transcript.py`：新增 workspace-relative + 无 cwd 保留绝对路径用例。
- [x] 5.4 `tests/integration/test_agent_hosts.py`：更新端到端测试用 cwd 下文件路径，断言 `materials/04_纪要.md`。

## 6. Capture 分阶段耗时（§5）

- [x] 6.1 `capture_service.py`：extraction（extractor.extract）/ relation（plan）/ persistence（commit_capture）三段 perf_counter 计时。
- [x] 6.2 `candidate_processing.py` `process`：validation/admission/lifecycle 三段循环内累加，填入 `CandidateProcessingResult.timing`。
- [x] 6.3 `capture_service.py` `memory.capture.completed`：新增 6 个 `*_duration_ms` 字段，未执行记 0。
- [x] 6.4 `docs/logging.md`：更新 completed 字段表。
- [x] 6.5 `tests/unit/test_logging_events.py`：completed 事件断言 6 个阶段耗时非负。

## 7. 日志事件顺序（§6）

- [x] 7.1 `capture_service.py`：relations_planned 的 log 移到 admission 之后、relation_candidates 之前。
- [x] 7.2 新增 `memory.capture.validation` 在 candidates 之后、admission 之前（见 2.4）。
- [x] 7.3 `docs/logging.md`：内容事件表新增 validation 行。

## 8. 回归与验收

- [x] 8.1 `uv run ruff check server/ agent/ tests/` 通过。
- [x] 8.2 `uv run pytest tests/contract/test_dependency_boundaries.py -q` 通过（4 passed）。
- [x] 8.3 `uv run pytest tests/unit tests/contract tests/integration -q` 通过（264 passed, 13 skipped）。
- [x] 8.4 `uv run python -m evals.runner --mode deterministic` 无回归。
- [ ] 8.5 真实联调：确认日志事件顺序 candidates->validation->admission->relations_planned、计数互相对上、source_uri 为 workspace-relative、分阶段耗时可见（人工验收，需真实 Claude Code + DeepSeek + PostgreSQL 环境）。
