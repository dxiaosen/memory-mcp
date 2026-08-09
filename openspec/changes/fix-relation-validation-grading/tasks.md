## 1. P0：Relation 校验 fatal/non-fatal 分级（§1/§2/§5）

- [x] 1.1 `automatic_relations.py`：`_admit` 返回 `(accepted, skipped, fatal_rejected)`；`invalid_source_expression`/`relation_endpoint_outside_catalog` 入 fatal，`relation_policy_mismatch` 等入 non-fatal `skipped`。
- [x] 1.2 `automatic_relations.py`：新增 `_relation_skip_reason`（non-fatal reason_code：relation_not_explicit/low_confidence/insufficient_evidence/negated/reversed_direction/duplicate/non_user_source）。
- [x] 1.3 `automatic_relations.py`：`plan` 仅当 fatal_rejected 非空才 retry；non-fatal-only 即 `completed`（skipped_count 计入 plan），Capture 继续；全部 attempt fatal 才 raise。

## 2. §3/§4：阈值不变 + exception/reason 一致

- [x] 2.1 `automatic_relations.py`：raise 消息改为 `relation validation failed: <reason_code>`（去硬编码）；`relation_extraction_attempt.failed` 的 error_message 与 reason 一致；retryable 仅 fatal（最后一次 false）。
- [x] 2.2 `relation_validation_rejected` content 事件记全部被拒/跳过 proposal 的真实 reason_code。
- [x] 2.3 Relation 自动保存阈值保持 `>= 0.90`（未改 `AUTO_RELATION_CONFIDENCE_THRESHOLD`）。

## 3. §6/§7：prompt 小收紧

- [x] 3.1 `backends.py` `_system_prompt`：原子化补「跨时期/跨行/跨 bullet 拆分」。
- [x] 3.2 `backends.py` `_system_prompt`：回声排除补「review/timeline status、缺失记忆解释」。

## 4. 测试

- [x] 4.1 `test_capture_service.py`：Case 4 policy_mismatch non-fatal skip（completed、1 次抽取、候选持久化）。
- [x] 4.2 `test_capture_service.py`：Case 5 低置信度 non-fatal skip（completed、不 retry、无关系）。
- [x] 4.3 更新 `test_invalid_automatic_relation_direction_fails_without_writes`：reversed-direction 改为 non-fatal skip（completed、候选持久化、无关系）。
- [x] 4.4 既有 fatal 用例（invalid_source_expression retry/incomplete、unknown endpoint fail）保持通过。

## 5. 文档与验收

- [x] 5.1 `docs/logging.md`：`relation_validation_rejected`/`relation_extraction_attempt.*` 更新 fatal/non-fatal 语义与 reason_code 列表。
- [x] 5.2 创建 `openspec/changes/fix-relation-validation-grading/`。
- [x] 5.3 `openspec-cn validate fix-relation-validation-grading --strict` 通过。
- [x] 5.4 `uv run ruff check .` 通过。
- [x] 5.5 `uv run pyright`（改动文件）0 错误。
- [x] 5.6 `uv run pytest tests/contract/test_dependency_boundaries.py` 通过。
- [x] 5.7 `uv run pytest -q` 通过（314 passed, 13 skipped）。
- [ ] 5.8 真实联调：Case 1 auto-save+relation、Case 2 uncertain->pending（relation skip 不拖垮）、Case 3 fatal fail-closed、Case 4/5 non-fatal skip、Case 6 跨会话 Recall 稳定（人工验收）。
