## 1. P0：Structured Output 窄范围 canonicalization（§5）

- [x] 1.1 `extraction/backends.py`：新增 `normalize_candidate_batch_output(value)`（拆单层 `{"candidates":{"candidates":[...]}}`，None/非 dict/非法 schema 抛 InvalidModelOutputError）。
- [x] 1.2 `extraction/backends.py`：新增 `_parse_candidate_batch(raw)`（归一化 + model_validate，失败附 raw 诊断 context）；`LangChainCandidateBackend.__call__` 改用之。
- [x] 1.3 `extraction/__init__.py`：导出 `normalize_candidate_batch_output`。

## 2. P0：失败诊断（§3）

- [x] 2.1 `extraction/backends.py`：`_structured_output_diagnostic(raw, exc)` 构造 `raw_type`/`raw_preview`/`error_type`/`error_message` context。
- [x] 2.2 `core/adapters/structured_model.py`：`StructuredCandidateExtractor.extract` 捕获 `InvalidModelOutputError` 时记 `memory.capture.structured_output.invalid` content 事件（model_id/prompt_version/schema_version + context）。

## 3. P0：合法空 Candidate（§6）

- [x] 3.1 `{"candidates": []}` 经归一化后合法通过（`normalize_candidate_batch_output` 原样返回 list）。
- [x] 3.2 backend 空候选返回 `[]`，Capture -> completed（0 计数），不进 invalid_candidate_output（既有 capture_service 逻辑保证，测试覆盖）。

## 4. P0：测试（§8）

- [x] 4.1 `tests/contract/test_extraction_backends.py`：normalize 单测（normal list / CandidateBatch 实例 / 双 wrapper 拆包 / None / 非 dict / malformed）。
- [x] 4.2 backend 双 wrapper 成功、空候选成功、None 失败附诊断 context 用例。

## 5. 文档与验收

- [x] 5.1 `docs/logging.md`：增 `memory.capture.structured_output.invalid` 事件。
- [x] 5.2 创建 `openspec/changes/fix-structured-output-stability/`。
- [x] 5.3 `openspec-cn validate fix-structured-output-stability --strict` 通过。
- [x] 5.4 `uv run ruff check .` 通过。
- [x] 5.5 `uv run pyright`（改动文件）0 错误。
- [x] 5.6 `uv run pytest tests/contract/test_dependency_boundaries.py` 通过。
- [x] 5.7 `uv run pytest -q` 通过（321 passed, 13 skipped）。
- [ ] 5.8 真实联调：DeepSeek Candidate structured output 重复测试稳定；核心 E2E A-D 通过（人工验收，需真实 DeepSeek + 物理隔离 Claude project memory 环境）。
