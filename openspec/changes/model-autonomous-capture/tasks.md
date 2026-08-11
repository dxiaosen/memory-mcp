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
- [x] transcript.py 删 `collect_turn_tool_uses`（保留 `extract_document_messages`）
  - 证据：`agent/src/memory_mcp_agent/transcript.py` 无 `collect_turn_tool_uses`，`extract_document_messages` 仍在
- [x] bridge.py `after_run_success` 删 `observed_at`/`document_messages` 参数
  - 证据：`agent/src/memory_mcp_agent/bridge.py` `async def after_run_success(self, context, *, user_input, final_output, subject_hint=None)`
- [x] bridge.py `AfterRunResult.event_id` 改名 `event_ref`，`_capture` 不再生成 event_id/observed_at
  - 证据：同文件 `@dataclass class AfterRunResult: event_ref: str`，`_capture` 调 `client.capture_completed_turn(conversation_id=, turn_id=, user_input=, final_output=, subject_hint=)`
- [x] client.py `capture_completed_turn` Protocol + MemoryMcpClient 签名简化
  - 证据：`agent/src/memory_mcp_agent/client.py` Protocol 与 impl 均为 `(conversation_id, turn_id, user_input, final_output, profile_id=None, subject_hint=None)`，不再传 event_id/contract_version/observed_at/messages

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
- [x] `uv run pytest tests/contract/test_dependency_boundaries.py` 通过（core 不动）
  - 证据：本地运行 `4 passed`
- [x] `uv run pytest -q` 全量通过
  - 证据：本地运行 `342 passed, 13 skipped`
- [x] `uv run python -m evals.runner --mode deterministic` 通过（无回归）
  - 证据：本地运行 isolation/lifecycle/safety pass_rate=1.0，failed_count=0
