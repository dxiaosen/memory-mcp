## 1. OpenSpec 变更提案

- [x] 1.1 创建 `openspec/changes/harden-candidate-semantics/` 及 spec delta。
- [x] 1.2 `openspec-cn validate harden-candidate-semantics --strict` 通过。

## 2. assertion_kind 一致性归一化

- [x] 2.1 `server/src/memory_mcp/core/application/candidate_processing.py`：新增 `_normalize_assertion_kind(reported, source_role, source_type)`，规则：tool/document/web source_type + 非外部 → external_fact；assistant + user_view/user_provided_fact → system_inference；其余 None。
- [x] 2.2 在构造 Candidate 前（`source_metadata` 之后、敏感检查之前）应用归一化，用 `resolved_assertion_kind` 覆盖 `proposal.assertion_kind`。
- [x] 2.3 归一化发生时记 DEBUG `memory.capture.candidate.assertion_normalized`（candidate_ref/memory_type/source_role/source_type/from_assertion_kind/to_assertion_kind）。
- [x] 2.4 `server/src/memory_mcp/extraction/backends.py` `_system_prompt`：强化 assertion_kind 指导（user_view 仅用户自身偏好；system_inference 用于自身分析；不要把推断标成 user_*）。
- [x] 2.5 验证 `review_service.py` confirm 路径只设 `verification_status=USER_CONFIRMED`，不重写 assertion_kind（materializer.record/duplicate/replacement 均保留 candidate.assertion_kind）。
- [x] 2.6 `tests/integration/test_capture_service.py`：`test_assistant_assertion_kind_is_normalized_to_system_inference` + `test_external_source_assertion_kind_is_normalized_to_external_fact` + `test_user_assertion_kind_is_not_normalized`。
- [x] 2.7 `tests/unit/test_logging_events.py`：`test_capture_logs_assertion_normalized_when_assistant_mislabeled` 断言 from/to/source_role。
- [x] 2.8 `docs/logging.md`：增 `memory.capture.candidate.assertion_normalized` 事件行。

## 3. 候选数量控制（三层）

- [x] 3.1 `server/src/memory_mcp/extraction/backends.py` `_system_prompt`：增"Aim for 5 to 10 candidates; never exceed 12, and prefer fewer or zero when evidence is thin or overlapping."
- [x] 3.2 `server/src/memory_mcp/profiles/investment_research.py` `capture_guidance`：增"Aim for 5 to 10 high-signal candidates per turn; never exceed 12"。
- [x] 3.3 `server/src/memory_mcp/profiles/__init__.py`：重算并注册 investment-research v1 新指纹（`0f63432b…`）。
- [x] 3.4 `server/src/memory_mcp/core/adapters/structured_model.py`：新增 `SOFT_CANDIDATE_LIMIT=12` 常量与 `_LOGGER`；`extract` 解析后若超 12 按 confidence 降序裁剪，记 DEBUG `memory.capture.candidates_truncated`（model_id/original_count/kept_count/soft_limit）。保留 `MAX_CANDIDATES=20` 硬上限。
- [x] 3.5 `tests/contract/test_capture_adapters.py`：`test_structured_model_adapter_trims_to_soft_limit_by_confidence` 断言 15→12 且按 confidence 降序。
- [x] 3.6 `docs/logging.md`：增 `memory.capture.candidates_truncated` 事件行。
- [x] 3.7 `uv run python -m evals.runner --mode deterministic` 通过（无回归）。

## 4. 文件来源 provenance（Host Adapter 解析 transcript）

- [x] 4.1 `agent/src/memory_mcp_agent/transcript.py`：新增纯函数模块 `extract_document_messages(transcript_path)`，解析 JSONL 还原 Read tool_use + tool_result，产出 `role=tool/source_type=document/source_uri/source_title` 消息字典；best-effort（失败返回空）；内容截断 8000 字符。
- [x] 4.2 `agent/src/memory_mcp_agent/hosts.py`：`AgentHookInput` 增 `transcript_path` 字段；`AgentTurnEvent` 增 `transcript_path` 字段；`normalize()` 透传；`_after` 调 `extract_document_messages` 并传给 `stage_capture`。
- [x] 4.3 `agent/src/memory_mcp_agent/state.py`：`TurnState` 增 `document_messages: list[dict[str, Any]]` 字段；`stage_capture` 增 `document_messages` 形参并随 outbox 持久化；冲突检测纳入比较。
- [x] 4.4 `agent/src/memory_mcp_agent/bridge.py`：`after_run_success` / `_capture` 增 `document_messages` 形参；fingerprint 纳入 `document_messages` 保证幂等。
- [x] 4.5 `agent/src/memory_mcp_agent/client.py`：`capture_completed_turn`（protocol + 实现）增 `document_messages` 形参；文档消息插在 user 与 assistant 之间。
- [x] 4.6 `tests/integration/test_agent_transcript.py`：新增解析器单元测试（提取/缺失/损坏/失败/截断/无 Read 调用）。
- [x] 4.7 `tests/integration/test_agent_hosts.py`：`test_transcript_path_surfaces_document_messages_in_capture` 端到端断言文档消息随 capture 请求投递。
- [x] 4.8 `docs/logging.md`：增 `agent_hook.transcript.parse_failed` / `agent_hook.transcript.document_messages_extracted` 事件行。

## 5. 零召回空渲染

- [x] 5.1 `server/src/memory_mcp/core/application/recall_service.py`：`_empty_result` 与内联 fallback 均返回 `rendered_context=""` / `estimated_tokens=0`。
- [x] 5.2 `tests/integration/test_lifecycle_recall.py:676`：断言 `other_result.rendered_context == ""` + `estimated_tokens == 0`。

## 6. 回归与验收

- [x] 6.1 `uv run ruff check server/ tests/ agent/` 通过（All checks passed）。
- [x] 6.2 `uv run pytest tests/contract/test_dependency_boundaries.py -q` 通过（4 passed，Core 自包含不变量维持）。
- [x] 6.3 `uv run pytest tests/unit tests/contract tests/integration -q` 通过（260 passed, 13 skipped）。
- [ ] 6.4 真实联调：文件事实 Candidate 显示 `source_type=document / source_title/source_uri 非空`（人工验收，需真实 Claude Code + DeepSeek + PostgreSQL 环境）。
