## Why

2026-08-09 E2E 出现 Candidate structured-output 不稳定：`CandidateBatch input = None` 与
`CandidateBatch.candidates Input should be a valid list, input_value={'candidates': [...]}`。
后者高度疑似 provider/SDK 偶发单层重复 wrapper `{"candidates": {"candidates": [...]}}`，导致
`CandidateBatch.model_validate` 失败 -> `InvalidModelOutputError` -> 整轮 `invalid_candidate_output`。
失败时缺乏 raw 响应诊断，无法定位是 None / schema malformed / 重复 wrapper 哪一层。

## What Changes

- **窄范围 canonicalization**：新增 `normalize_candidate_batch_output(value)`，在 `model_validate`
  前拆掉单层重复 wrapper `{"candidates": {"candidates": [...]}}` -> `{"candidates": [...]}`；
  合法 `{"candidates": []}` / `{"candidates": [{...}]}` 原样返回；None / 非 dict / schema 非法抛
  `InvalidModelOutputError`（可重试）。只允许一层明确 wrapper，禁止递归 unwrap / 猜 schema。
- **结构化输出失败诊断**：`_parse_candidate_batch` 失败时把 `raw_type`/`raw_preview`/`error_type`/
  `error_message` 附到 `InvalidModelOutputError.context`；`StructuredCandidateExtractor.extract` 捕获后
  记 `memory.capture.structured_output.invalid` content 事件（含 `model_id`/`prompt_version`），
  开发态记录完整 raw 响应，生产态沿用 content log 门控。
- **合法空 Candidate**：`{"candidates": []}` 经归一化后合法通过，Capture -> completed（0 计数），
  不进 `invalid_candidate_output`。
- **测试**：normal/double-wrapper/empty/None/非 dict/malformed 用例 + backend 双 wrapper 与空候选成功用例。

## Capabilities

### New Capabilities

- `structured-output-stability`：规定 Candidate 结构化输出的窄范围 wrapper canonicalization、
  失败诊断日志、合法空候选处理。

### Modified Capabilities

无。

## Impact

- 提取层：`extraction/backends.py` 增 `normalize_candidate_batch_output`/`_parse_candidate_batch`/
  `_structured_output_diagnostic`，`LangChainCandidateBackend.__call__` 改用 `_parse_candidate_batch`；
  `extraction/__init__.py` 导出 `normalize_candidate_batch_output`。
- Core 适配：`core/adapters/structured_model.py` `StructuredCandidateExtractor.extract` 增
  `structured_output.invalid` content 诊断日志。
- 文档：`docs/logging.md` 增 `memory.capture.structured_output.invalid` 事件。
- 不改 Prompt、Admission/Relation/Recall 阈值、MCP DTO、DB schema、Core 自包含不变量。
