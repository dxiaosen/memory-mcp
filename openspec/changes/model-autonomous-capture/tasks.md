# Tasks: 模型自主调用 capture（Phase 1）

## 1. Server 侧 capture 契约简化

- [x] schemas.py 删 `CompletedTurnEventV1`，新建 `CompletedTurnInputV1`
  - 证据：`server/src/memory_mcp/schemas.py` `class CompletedTurnInputV1(StrictDto)`，含 `input_fingerprint()` + `to_turn_envelope(owner_id, max_characters, clock)`
- [x] 新增 `_derive_event_id` + `_CONTRACT_VERSION` 模块级常量
  - 证据：同文件 `def _derive_event_id(owner_id, conversation_id, turn_id) -> str` 返回 `memory-agent:{sha256}`，`_CONTRACT_VERSION = "1"`
- [x] `RoleMessageV1` 保留（DTO，暂不删，Phase 2 document provenance 恢复时可能用）
  - 证据：同文件 `class RoleMessageV1(StrictDto)` 仍在
- [x] `MemoryService` 加公开 `clock` 属性
  - 证据：`server/src/memory_mcp/core/application/service.py` `@property def clock(self) -> Callable[[], datetime]: return self._clock`
- [x] capture.py 重写 `capture_completed_turn` 签名（删 event_id/contract_version/observed_at/messages，加 user_input/final_output）
  - 证据：`server/src/memory_mcp/tools/capture.py` `async def capture_completed_turn(conversation_id, turn_id, user_input, final_output, ctx, profile_id=None, subject_hint=None)`
- [x] capture.py description 引导 gate（仅持久信号时调用）
  - 证据：同文件 description 含 "Call this ONLY after a turn where the user stated or revised a durable fact..."

## 2. Agent 侧移除 Stop hook 捕获路径

- [x] hosts.py `_after` 改 no-op（`_noop_after` 返回 `AgentHookOutcome()`，记 `agent_hook.after_run.noop`）
  - 证据：`agent/src/memory_mcp_agent/hosts.py` `handle()` after_run 分支调 `self._noop_after`，返回 `AgentHookOutcome()`
- [x] hosts.py 删 `_after`/`_deliver_staged`/`_finish_delivery`/`_retry_one_pending`/`_is_inspect_or_manage_turn`/`_MEMORY_MANAGEMENT_TOOLS`
  - 证据：grep 确认这些符号已不在 hosts.py
- [x] hosts.py `_before` 不再 `self._state.save(TurnState(...))`
  - 证据：`_before` 内无 `self._state.save` 调用
- [x] transcript.py 删 `collect_turn_tool_uses`（后续整文件删除见 §6）
  - 证据：`agent/src/memory_mcp_agent/transcript.py` 不存在
- [x] bridge.py `after_run_success` 删 `observed_at`/`document_messages` 参数（后续整方法删除见 §6）
  - 证据：`agent/src/memory_mcp_agent/bridge.py` 无 `after_run_success`
- [x] bridge.py `AfterRunResult.event_id` 改名 `event_ref`，`_capture` 不再生成 event_id/observed_at（后续 `AfterRunResult`/`_capture` 整体删除见 §6）
  - 证据：同文件无 `AfterRunResult`/`_capture`
- [x] client.py `capture_completed_turn` Protocol + MemoryMcpClient 签名简化（后续整体删除见 §6）
  - 证据：`agent/src/memory_mcp_agent/client.py` 无 `capture_completed_turn`

## 3. 测试更新

- [x] `test_server_contracts.py`：`CompletedTurnEventV1` → `CompletedTurnInputV1`，payload 改简化字段，`to_turn_envelope` 补 owner_id/clock
  - 证据：`tests/integration/test_server_contracts.py` `test_completed_turn_is_strict_versioned_and_fingerprint_stable` 用 `CompletedTurnInputV1.model_validate` + `to_turn_envelope(owner_id=..., clock=lambda: fixed_time)`
- [x] `test_server_transport.py` `_event()` 改简化字段，unsupported_contract_version 测试改测 contract_version 被 forbid 拒
  - 证据：`_event()` 返回 `{profile_id, conversation_id, turn_id, user_input, final_output}`；`{**_event(), "contract_version": "2"}` 断言 `isError is True`
- [x] `test_server_transport.py` cross-host 测试：Stop 后加 MCP capture 调用模拟模型自主调用
  - 证据：`test_agent_hook_adapter_cross_host_transport_and_owner_isolation` 内 `codex_capture`/`generic_capture` via `anyio.run(_with_session, ...)`
- [x] `test_agent_bridge.py`：删 `observed_at` 参数，`call["event_id"]` → `call["conversation_id"]`
  - 证据：`test_before_and_after_run...` 无 `observed_at=`；`test_after_run_retries...` 断言 `len({c["conversation_id"] for c in client.capture_calls}) == 1`
- [x] `test_agent_settings.py`：删 `event_id`/`observed_at` 参数
  - 证据：`client.capture_completed_turn(profile_id=None, conversation_id=..., turn_id=..., user_input=..., final_output=...)`
- [x] `test_agent_hosts.py`：重写 Stop→capture 测试为 no-op 行为，删 document_messages/inspect-skip/outbox-retry 测试
  - 证据：`tests/integration/test_agent_hosts.py` 9 passed，含 `test_after_run_is_noop_regardless_of_transcript`（Stop 为 no-op），原 document_messages/inspect-skip/outbox-retry 测试已删（525→48 行）

## 4. 文档更新

- [x] CLAUDE.md 测试边界反向重写（capture 模型自主调用）
  - 证据：`CLAUDE.md` "捕获由模型自主决定是否调用 capture_completed_turn"
- [x] design.md §10 流程图 + 新增 §10.7
  - 证据：`docs/design.md` §10.2 mermaid 改 no-op + §10.7 模型自主调用 capture
- [x] logging.md 删 `capture.skipped` inspect reason + `pending_retry` 事件，加 `after_run.noop`
  - 证据：`docs/logging.md` 行 231 区域
- [x] README.md mermaid 图改 model → capture
  - 证据：`docs/README.md` `MODEL[模型自主决定] -->|有持久信号 → capture_completed_turn| SRV`

## 5. 质量门禁

- [x] `uv run ruff check .` 通过
  - 证据：本地运行 `All checks passed!`
- [x] `uv run pyright agent/src/memory_mcp_agent/` 通过
  - 证据：本地运行 `0 errors, 0 warnings, 0 informations`
- [x] `uv run pytest tests/contract/test_dependency_boundaries.py` 通过（core 不动）
  - 证据：本地运行 `4 passed`
- [x] `uv run pytest -q` 全量通过
  - 证据：本地运行 `325 passed, 13 skipped`（死代码测试删除后较 Phase 1 初版 342 减少）
- [x] `uv run python -m evals.runner --mode deterministic` 通过（无回归）
  - 证据：本地运行 isolation/lifecycle/safety pass_rate=1.0，failed_count=0

## 6. Agent 死代码清理（Phase 1 收尾）

Phase 1 移除 Stop hook capture 路径后，agent 包留下约 1200 行无生产调用方的死代码。
本节记录清理范围（§2 中"保留"的过渡表述以此为准）：

- [x] `bridge.py` 删 `after_run_success`/`_capture`/`AfterRunResult`/`_AfterTask`/`_event_ref`，仅保留 BeforeRun 召回链
  - 证据：`agent/src/memory_mcp_agent/bridge.py` 仅有 `before_run`/`_recall`/`BeforeRunResult`/`_BeforeTask`，无 after_run_success
- [x] `runner.py` 整文件删除（`HookedAgentRunner`/`RunnerResult` 无生产调用方）
  - 证据：`agent/src/memory_mcp_agent/runner.py` 不存在；`__init__.py` 不导出 `HookedAgentRunner`/`RunnerResult`
- [x] `client.py` 删 `capture_completed_turn`（Protocol + impl）/`CaptureSummary`/`CaptureResponse`，保留 `recall_memory`
  - 证据：`agent/src/memory_mcp_agent/client.py` 无 capture_completed_turn/CaptureResponse/CaptureSummary
- [x] `transcript.py` 整文件删除（`extract_document_messages` 无生产调用方）
  - 证据：`agent/src/memory_mcp_agent/transcript.py` 不存在
- [x] `state.py` 瘦身：删 `TurnState`/`TurnStateConflictError`/`save`/`load`/`stage_capture`/`pending_captures`/`delete`，仅保留 `TurnStateStore` 构造 + `for_working_directory` + `cleanup_expired`（过渡期清理残留旧文件）
  - 证据：`agent/src/memory_mcp_agent/state.py` 仅含 `TurnStateError`/`_LegacyTurnState`/`TurnStateStore.cleanup_expired`
- [x] `settings.py` 删 `timeout_seconds`/`capture_timeout_seconds`/`capture_max_attempts`/`capture_retry_delay_seconds`（capture 不再经 agent 客户端）
  - 证据：`agent/src/memory_mcp_agent/settings.py` 仅含 `recall_timeout_seconds` 等召回侧设置
- [x] `__init__.py` 导出表清理死符号
  - 证据：`agent/src/memory_mcp_agent/__init__.py` `__all__` 不含 AfterRunResult/CaptureResponse/HookedAgentRunner/RunnerResult/TurnStateConflictError
- [x] 测试清理：删 `test_agent_transcript.py`（整文件）、`test_agent_bridge.py` 删 AfterRun/runner 测试保留 BeforeRun、`test_agent_state.py` 仅保留 cleanup_expired、`test_agent_settings.py` 删 capture 超时/capture 工具测试、`test_agent_hosts.py` 删 `_FakeClient.capture_completed_turn`、`test_postgresql_transport.py` `_event()` 改新契约 + HookedAgentRunner 测试改直接 MCP 调用
  - 证据：上述文件 grep 无 capture_completed_turn（agent 侧）/HookedAgentRunner/extract_document_messages
- [x] 文档清理：`logging.md` 删 capture.attempt/capture.retry/capture.exhausted/capture.fail_open/transcript.*/turn_state.read_failed/turn_state.invalid 事件；`config.md`/`.env.example` 删 capture 超时；`design.md` §10.3-10.5 去重 + 删 capture 重试常量；`agents.md` §1/§4.2、`deploy.md`、`README.md`、`usage.md`、`e2e-report.md` 去 HookedAgentRunner 引用；`examples/hook_runner.py` 改 BeforeRun-only
  - 证据：grep 全仓 `HookedAgentRunner` 仅余 `docs/e2e-report.md` 历史标注（已加 Phase 1 注记）
