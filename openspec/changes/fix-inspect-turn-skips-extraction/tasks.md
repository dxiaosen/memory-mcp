# Tasks: inspect/manage turn 跳过抽取

## 1. transcript.py 新增 tool_use 收集

- [x] 新增 `collect_turn_tool_uses(transcript_path, *, cwd, user_prompt) -> set[str]`
  - 证据：`agent/src/memory_mcp_agent/transcript.py` `def collect_turn_tool_uses(...) -> set[str]`，复用 `_iter_jsonl`/`_slice_current_turn`，返回当前轮次 assistant 块所有 tool_use name 集合，解析失败返回空 set
- [x] 解析失败/无 transcript 时返回空集合（best-effort 不抛）
  - 证据：同函数 `if not transcript_path: return set()` 与 except 分支 `return set()`

## 2. hosts.py _after 加跳过判断

- [x] 新增 `_MEMORY_MANAGEMENT_TOOLS` 常量（11 个管理工具，不含 recall_memory/capture_completed_turn）
  - 证据：`agent/src/memory_mcp_agent/hosts.py` `_MEMORY_MANAGEMENT_TOOLS: frozenset[str] = frozenset({...})`
- [x] 新增 `_is_inspect_or_manage_turn` 辅助函数（无 transcript 返回 False）
  - 证据：同文件 `def _is_inspect_or_manage_turn(...) -> bool:`，`if not transcript_path: return False`
- [x] `_after` 在 `extract_document_messages` 后、`stage_capture` 前加跳过判断
  - 证据：同文件 `_after` 内 `if _is_inspect_or_manage_turn(event.transcript_path, event.cwd, saved.prompt): return self._skip_after("inspect_or_manage_turn", ...)`
- [x] 走既有 `_skip_after` 路径，reason_code=`inspect_or_manage_turn`
  - 证据：同上，复用 `_skip_after` 记 `agent_hook.capture.skipped` WARNING

## 3. 测试

- [x] `test_inspect_turn_with_memory_management_tool_skips_capture`：含 search_memories 的 transcript → 跳过 capture，`warning_code == "inspect_or_manage_turn"`，`capture_calls == []`
  - 证据：`tests/integration/test_agent_hosts.py` 该测试断言 `output == AgentHookOutcome(warning_code="inspect_or_manage_turn")` 且 `assert client.capture_calls == []`
- [x] `test_business_turn_with_only_recall_memory_still_captures`：transcript 含 Read（非 memory 管理工具）→ 正常 capture，`capture_calls` 非空
  - 证据：同文件该测试断言 `output == AgentHookOutcome()` 且 `assert len(client.capture_calls) == 1`
- [x] `uv run pytest tests/integration/test_agent_hosts.py -q` → 16 passed（原 14 + 新 2）
  - 证据：本地运行结果
- [x] 全量 `uv run pytest -q` → 349 passed, 13 skipped（不回退）
  - 证据：本地运行结果
- [x] `uv run ruff check .` 通过
  - 证据：本地运行结果（All checks passed）
- [x] `uv run pyright` 改动文件通过（0 errors）
  - 证据：本地运行结果

## 4. 文档

- [x] `docs/design.md` §10.2 AfterRun 流程图加 inspect/manage 判断分支 + 说明段
  - 证据：`docs/design.md` §10.2 mermaid 含 `CHK{inspect/manage turn?}` 分支与说明段
- [x] `docs/logging.md` `agent_hook.capture.skipped` 行补 `inspect_or_manage_turn` reason_code
  - 证据：`docs/logging.md` 该行含 `reason_code` 三值说明
