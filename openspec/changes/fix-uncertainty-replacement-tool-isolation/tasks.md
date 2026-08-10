## 1. A1：explicit_uncertainty -> Pending

- [x] 1.1 `admission.py`：`has_explicit_uncertainty(candidate)` + `_EXPLICIT_UNCERTAINTY_RE`；`decide` 在 system_inference 前增 uncertainty -> PENDING(explicit_uncertainty)。
- [x] 1.2 测试：`test_explicit_uncertainty_goes_pending_not_auto_save`。

## 2. A2：replacement target fallback

- [x] 2.1 `candidate_processing.py`：`_REPLACEMENT_FALLBACK_THRESHOLD=0.45`；`_resolve_semantic_target` 对 `_is_explicit_replacement` 用宽松阈值查旧 active memory。
- [x] 2.2 `backends.py` prompt：补「明确修正时输出一个完整 thesis candidate，不拆分」。

## 3. C1/C3：tool scope 隔离

- [x] 3.1 `tools/shared.py`：`TOOL_SCOPES` 权威映射 + `visible_tool_names` + `authorize_tool_call`。
- [x] 3.2 `tools/memory.py`：`link_memories` WRITE -> REVIEW。
- [x] 3.3 `app.py`：`MemoryMcpServer.list_tools` 按 principal scopes 过滤（无 auth context 回退全部）。

## 4. D2：relation semantic dedupe 回归

- [x] 4.1 确认 InMemory `link_relation` + PostgreSQL partial unique index + `_insert_relation` ON CONFLICT 已实现语义去重。
- [x] 4.2 测试：`test_manual_link_dedupes_with_existing_active_semantic_relation`。

## 5. E：revoke cascade 回归

- [x] 5.1 确认 `revoke` `stale_at=now()` + `_stale_revision_relations` 已实现（上一轮）。
- [x] 5.2 测试：`test_revoke_memory_cascades_active_relation_to_stale`（上一轮已补）。

## 6. 文档与验收

- [x] 6.1 创建 `openspec/changes/fix-uncertainty-replacement-tool-isolation/`。
- [x] 6.2 `openspec-cn validate fix-uncertainty-replacement-tool-isolation --strict` 通过。
- [x] 6.3 `uv run ruff check .` 通过。
- [x] 6.4 `uv run pyright`（改动文件）0 错误。
- [x] 6.5 `uv run pytest tests/contract/test_dependency_boundaries.py` 通过。
- [x] 6.6 `uv run pytest -q` 通过（325 passed, 13 skipped）。
- [ ] 6.7 真实联调：Test 1-10（空库/长期基准/fresh recall/uncertain->pending/thesis replacement/fresh recall latest/semantic relation/explicit management/manual relation/revoke cascade）人工验收。
