## 1. P0：source_expression 两级空白归一化（§1）

- [x] 1.1 `candidate_processing.py`：移除单级 `_normalize_source_text`，新增 `_normalize_whitespace`/`_normalize_compact`/`_source_expression_in`。
- [x] 1.2 `process`：预计算 `source_whitespace`/`source_compact`，校验用两级 containment。
- [x] 1.3 `_source_metadata`：per-message 用 `_source_expression_in`。
- [x] 1.4 `test_capture_service.py`：删换行（compact）通过、CRLF/连续空格（whitespace）通过、拼接 bullet 仍拒绝。

## 2. P0/P1：Candidate 原子化与来源保真（§2/§5/§6，Prompt）

- [x] 2.1 `backends.py` `_system_prompt`：原子化、事实完整支撑、关系语义不入事实 content。
- [x] 2.2 `backends.py`：research_preference 仅用户来源；external_fact 来源优先 user>tool/document>assistant。
- [x] 2.3 `backends.py`：不要抽取 Assistant 回声/元信息/未采纳框架（§4 prompt 部分）。

## 3. P0：抽取有界重试（§3）

- [x] 3.1 `capture_service.py`：`_EXTRACTION_MAX_ATTEMPTS=3` + `_extract_candidates` 重试循环，仅捕获 `InvalidModelOutputError`。
- [x] 3.2 `capture_service.py`：`extraction_attempt.started/failed/completed` 事件（capture_id/attempt/max_attempts/duration_ms/error_type/retryable）。
- [x] 3.3 `tests/support/fakes.py`：`FakeCandidateExtractor` 增 `failure_exc`（默认 InvalidModelOutputError）。
- [x] 3.4 `test_capture_service.py`：Case D 重试成功（2 次）+ 全失败 incomplete。
- [x] 3.5 `test_logging_events.py`：extraction_attempt 事件断言。

## 4. P1：Assistant 回声丢弃（§4）

- [x] 4.1 `candidate_processing.py`：`_is_assistant_restatement`（精确命中+content 复述，语义兜底）+ `_content_restates`；生命周期段 discard `assistant_restatement`。
- [x] 4.2 `test_capture_service.py`：Case E assistant 回声 discard、用户重述走 duplicate/evidence。

## 5. P1：Recall 查询归一化（§7）

- [x] 5.1 `recall_service.py`：`_normalize_recall_query`（子句切分剔除指令/文件列表，保留实体，空回退原文）。
- [x] 5.2 `recall`：对 query.query 归一化用于 search_text；recall.input 记 normalized_query。
- [x] 5.3 `tests/unit/test_recall_query_normalization.py`：长 Prompt 剔除指令、Case B 保留实体、纯实体不变、空回退。

## 6. 文档与验收

- [x] 6.1 `docs/logging.md`：extraction_attempt 三事件 + recall.input normalized_query。
- [x] 6.2 创建 `openspec/changes/fix-extraction-reliability-source-fidelity/`（proposal/specs/design/tasks）。
- [x] 6.3 `openspec-cn validate fix-extraction-reliability-source-fidelity --strict` 通过。
- [x] 6.4 `uv run ruff check .` 通过。
- [x] 6.5 `uv run pyright`（改动文件）0 错误。
- [x] 6.6 `uv run pytest tests/contract/test_dependency_boundaries.py` 通过。
- [x] 6.7 `uv run pytest -q` 通过（306 passed, 13 skipped，含两级归一化/重试/回声/查询归一化用例）。
- [ ] 6.8 真实联调：Case A 跨行中文长期判断不再 invalid_source_expression 且能保存；Case B 首次 Recall result_count>0；Case C 南美铜矿仍 0；Case D 重试成功；Case E 回声 discard；Case F 关系端点 active 后 relation_accepted>0、timeline hop_count>0（人工验收，需真实环境）。
