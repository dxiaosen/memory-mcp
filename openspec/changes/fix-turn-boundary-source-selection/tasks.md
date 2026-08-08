## 1. P0-1：CompletedTurnEvent 只含当前 Turn（Agent Host Adapter）

- [x] 1.1 `agent/src/memory_mcp_agent/transcript.py`：`extract_document_messages` 增 `user_prompt` 参数；新增 `_slice_current_turn`（按最近用户文本消息切片）与 `_user_text`（区分用户文本与 tool_result 条目）、`_normalize_ws` 辅助。
- [x] 1.2 `transcript.py`：`_collect_read_tool_uses`/`_match_tool_results` 改为对切片后的 `turn_entries` 操作。
- [x] 1.3 `agent/src/memory_mcp_agent/hosts.py`：`_after` 调用 `extract_document_messages` 传 `user_prompt=saved.prompt`。
- [x] 1.4 `tests/integration/test_agent_transcript.py`：多轮切片用例（turn2 不含 turn1 文档；无工具调用返回空；无 user_prompt 回退最后一条用户文本消息）。
- [x] 1.5 `tests/integration/test_agent_hosts.py`：两轮 E2E，断言第二轮 `document_messages==[]`、不重复第一轮文档。

## 2. P0-3：source_expression 空白归一化校验（Core）

- [x] 2.1 `server/src/memory_mcp/core/application/candidate_processing.py`：新增 `_SOURCE_WHITESPACE_RE` 与 `_normalize_source_text`（仅 `\s+`->单空格 + trim，不做 NFKC/casefold）。
- [x] 2.2 `process`：循环前 `normalized_source = _normalize_source_text(redacted_source)`；校验改为 `_normalize_source_text(proposal.source_expression) not in normalized_source`。
- [x] 2.3 `_source_metadata`：per-message 改为 `normalize(source_expression) in normalize(redacted)`。
- [x] 2.4 `tests/integration/test_capture_service.py`：跨行空格差异通过校验用例 + 拼接 bullet 仍 invalid 用例（§4 严格性）。

## 3. P0-2：用户原文优先于 Assistant 复述（Core + Prompt）

- [x] 3.1 `candidate_processing.py`：新增 `_select_source_message`，按 `user > tool > assistant` 显式优先级选中；`_source_metadata` 调用之。
- [x] 3.2 `server/src/memory_mcp/extraction/backends.py` `_system_prompt`：增「用户自身判断取用户原文逐字片段、优先级 user>tool/document>assistant」引导。
- [x] 3.3 `tests/integration/test_capture_service.py`：同一表达式命中 user+assistant 时绑定 user、`user_view`、auto_save 用例。

## 4. P0-5：Candidate 原子化（Prompt）

- [x] 4.1 `backends.py` `_system_prompt`：增「source_expression 必须来自单条消息单个连续 span，禁止拼接多 bullet/外部来源」。

## 5. P0-9：Team Extraction 去重（Core，低优先级）

- [x] 5.1 `server/src/memory_mcp/core/application/team_extraction_service.py`：新增 `_dedup_team_configs`，`__init__` 存 config 前按 `team_owner_id` 去重、成员取并集保序。
- [x] 5.2 `tests/integration/test_team_extraction.py`：重复 team_owner_id 配置去重为单次、成员并集用例。

## 6. OpenSpec 与验收

- [x] 6.1 创建 `openspec/changes/fix-turn-boundary-source-selection/`（proposal/specs/design/tasks）。
- [x] 6.2 `openspec-cn validate fix-turn-boundary-source-selection --strict` 通过。
- [x] 6.3 `uv run ruff check .` 通过。
- [x] 6.4 `uv run pytest tests/contract/test_dependency_boundaries.py` 通过（Core 自包含不变量）。
- [x] 6.5 `uv run pytest -q` 通过（295 passed, 13 skipped，含新两轮 E2E + 归一化 + 用户优先 + team 去重用例）。
- [ ] 6.6 真实联调：确认第二轮 `message_count=2`、`source_role=user`/`auto_saved_count>0`、`invalid_source_expression` 不因纯换行误杀、team_count 不再虚高（人工验收，需真实 Claude Code + DeepSeek + PostgreSQL 环境）。
