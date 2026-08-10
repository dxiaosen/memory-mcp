## 1. P0：Relation best-effort（§3/§3.5）

- [x] 1.1 `automatic_relations.py`：plan 重试耗尽 / 模型结构错误 -> `_best_effort_failure_plan`（记 `relation_extraction_failed`，返回空 plan，不 raise）。
- [x] 1.2 `capture_service.py`：`commit_capture` Relation 写入失败 -> 记 `relation_commit_failed` + 重试 commit（relations=()）；`committed_relations` 用于日志。
- [x] 1.3 测试：`test_relation_fatal_exhausted_best_effort_preserves_candidates`、`test_unknown_relation_endpoint_best_effort_preserves_candidates`、`test_relation_write_failure_best_effort_preserves_candidates`、更新 `test_replacement_and_stale_transition_roll_back_together`。

## 2. P0：Candidate-level discard（§4）

- [x] 2.1 `candidate_processing.py`：`validate_memory_type`/`validate_business_progress` 包 try/except -> discard `invalid_memory_type`/`invalid_business_progress`/`invalid_candidate_field`。
- [x] 2.2 测试：`test_single_candidate_invalid_memory_type_discarded_not_batch_fail`、`test_invalid_model_type_discarded_safely`；更新 `test_investment_profile_blocks_transactions_and_invalid_progress`、`test_capture_incomplete_logs_duration_and_failure_code`。

## 3. P0：revoke 级联 stale_at 修复（§5）

- [x] 3.1 `postgresql/repository.py`：`revoke` 的 `stale_at` 由 `revision_created_at` 改 `datetime.now(UTC)`。
- [x] 3.2 `in_memory.py`：`revoke` 的 `stale_at` 同步改为 `datetime.now(UTC)`。
- [x] 3.3 测试：`test_revoke_memory_cascades_active_relation_to_stale`。

## 4. mutation 工具边界（§6/§7/§8）

- [x] 4.1 `tools/memory.py`：`revoke_memory`/`link_memories`/`batch_confirm_pending` description 增边界提示。
- [x] 4.2 `tools/review.py`：`confirm_pending_memory`/`reject_pending_memory` description 增边界提示。
- [x] 4.3 `CLAUDE.md`：增「业务更新不是记忆管理命令」段。

## 5. 文档与验收

- [x] 5.1 `docs/logging.md`：增 `relation_extraction_failed`/`relation_commit_failed`；更新 `relation_extraction_attempt.failed` best-effort 语义。
- [x] 5.2 创建 `openspec/changes/fix-boundary-isolation-relation-best-effort/`。
- [x] 5.3 `openspec-cn validate fix-boundary-isolation-relation-best-effort --strict` 通过。
- [x] 5.4 `uv run ruff check .` 通过。
- [x] 5.5 `uv run pyright`（改动文件）0 错误。
- [x] 5.6 `uv run pytest tests/contract/test_dependency_boundaries.py` 通过。
- [x] 5.7 `uv run pytest -q` 通过（323 passed, 13 skipped）。
- [ ] 5.8 真实联调：核心 E2E 6 步（baseline auto-save / fresh recall / thesis revision / recall latest / semantic relation / final recall）+ revoke 级联无 CheckViolation + Claude 不主动 mutation（人工验收）。
