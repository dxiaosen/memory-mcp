## 1. OpenSpec 变更提案

- [x] 1.1 创建 `openspec/changes/harden-capture-reliability/` 及 spec delta。
- [x] 1.2 `openspec-cn validate harden-capture-reliability --strict` 通过。

## 2. Agent 双超时与 per-request timeout

- [x] 2.1 `agent/src/memory_mcp_agent/settings.py`：`timeout_seconds` 保留为弃用别名，新增 `recall_timeout_seconds: float = 15.0` + `capture_timeout_seconds: float = 70.0`（均 gt=0, le=300）。
- [x] 2.2 `agent/src/memory_mcp_agent/client.py`：`_call_tool`/`call_tool`/`_request` 增 `timeout: float | None = None` 形参，传给 httpx 请求级 `timeout=`；`recall_memory` 传 `recall_timeout_seconds`，`capture_completed_turn` 传 `capture_timeout_seconds`；`_ensure_http_client` 客户端级默认改用 `recall_timeout_seconds`。
- [x] 2.3 `agent/.env.example`：注释双超时；`docs/config.md` §4：加 `MEMORY_HOOK_RECALL_TIMEOUT_SECONDS` / `MEMORY_HOOK_CAPTURE_TIMEOUT_SECONDS` 两行。
- [x] 2.4 `tests/integration/test_agent_settings.py`：`test_agent_uses_separate_recall_and_capture_timeouts` + `test_agent_timeout_env_overrides_apply` 断言默认值与 env 覆盖。

## 3. Agent attempt 调试事件

- [x] 3.1 `agent/src/memory_mcp_agent/bridge.py`：for attempt 循环首记 `agent_hook.capture.attempt.started`（event_ref/attempt/timeout_seconds）。
- [x] 3.2 成功返回记 `agent_hook.capture.attempt.completed`（event_ref/attempt/duration_ms/replayed/status）。
- [x] 3.3 except 块记 `agent_hook.capture.attempt.failed`（event_ref/attempt/duration_ms/error_type/error_code/retryable）。
- [x] 3.4 `docs/logging.md`：agent 事件表增上述三事件行。

## 4. source_expression 降级为 DISCARD

- [x] 4.1 `server/src/memory_mcp/core/application/candidate_processing.py:301`：`raise` → `outcomes.append(CaptureOutcome(candidate_id, DISCARD, "invalid_source_expression"))` + `continue`。
- [x] 4.2 `candidate_processing.py:322`：`raise` → DISCARD + reason_code=`ambiguous_source_message` + `continue`。
- [x] 4.3 `tests/integration/test_capture_service.py::test_unmatched_source_expression_discards_only_that_candidate`：注入 source_expression 不匹配的 proposal，断言该条 DISCARD、其余正常。
- [x] 4.4 `uv run pytest tests/contract/test_dependency_boundaries.py -q` 通过（4 passed）。

## 5. invalid_output 诊断补全

- [x] 5.1 `server/src/memory_mcp/core/exceptions.py`：`InvalidModelOutputError` 增 `context: dict[str, Any] | None = None` 可选构造参数。
- [x] 5.2 `server/src/memory_mcp/core/adapters/structured_model.py`：`_required_text`/`_confidence`/`_uuid_value`/`_optional_datetime`/`_enum_value` 各 raise 处带 context（field/value/reason）。
- [x] 5.3 `server/src/memory_mcp/core/application/capture_service.py` `_validation_errors`：优先读 `exc.context`，其次 pydantic cause 链，最后异常消息兜底。
- [x] 5.4 （实现合并入 5.3）违规字段经 `error_detail` 暴露，非单独 `source_expression`/`candidate_index` 字段——更简单且 logging.md:119 已更新说明。
- [x] 5.5 `docs/logging.md:119`：更新 `memory.capture.invalid_output.error_detail` 说明（context 优先 + 消息兜底，保证非 null）。
- [x] 5.6 `tests/unit/test_logging_events.py::test_capture_invalid_output_logs_error_detail_not_null`：断言 error_detail 非空（context 优先 + 无 context 时消息兜底）。

## 6. 回归与验收

- [x] 6.1 `uv run ruff check .` + `uv run pyright`（改动文件）通过。
- [x] 6.2 `uv run pytest tests/contract/ tests/unit/ tests/integration/test_agent_settings.py tests/integration/test_capture_service.py -q` 通过（118 passed, 11 skipped）。
- [ ] 6.3 真实联调：30–40s capture 产生 `CallToolRequest=1 / capture.completed=1 / replay=0`（人工验收，需真实 DeepSeek + PostgreSQL 环境）。
