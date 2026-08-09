## 1. P0：RelationExtractor 有界重试 + 三级校验 + rejected 日志（§1）

- [x] 1.1 `automatic_relations.py`：新增 `_normalize_whitespace`/`_normalize_compact`/`_relation_source_expression_in`（raw->whitespace->compact）。
- [x] 1.2 `automatic_relations.py`：新增 `RejectedRelation` dataclass；`_admit` 改为收集 `rejected`（不就地 raise），返回 `(accepted, skipped_count, rejected)`。
- [x] 1.3 `automatic_relations.py`：`plan` 把 extract+admit 包进重试循环（`_RELATION_EXTRACTION_MAX_ATTEMPTS=3`），记 `relation_extraction_attempt.started/failed/completed` + `relation_validation_rejected` content；全部 attempt 有 rejected 才 raise InvalidModelOutputError。
- [x] 1.4 `tests/support/fakes.py`：`FakeRelationExtractor` 增 `failure_exc`（默认 InvalidModelOutputError）。
- [x] 1.5 `test_capture_service.py`：Case E 重试成功（2 次）+ 全失败 incomplete；更新 `test_relation_provider_failure_reprocesses_without_duplicate_relation` 用 `failure_exc=RuntimeError`。

## 2. P1：operational_instruction 丢弃（§4）

- [x] 2.1 `candidate_processing.py`：新增 `_OPERATIONAL_INSTRUCTION_RE`/`_EXPLICIT_DURABLE_PREFERENCE_RE`/`_is_operational_instruction`；`process` 校验段 discard `operational_instruction`（类型无关）。
- [x] 2.2 `backends.py` `_system_prompt`：operational instruction 非 research_preference 引导。
- [x] 2.3 `test_capture_service.py`：Case B operational 丢弃 + 显式持久偏好保留用例。

## 3. P1：Recall 查询归一化修复 + operational-only 跳过（§5）

- [x] 3.1 `recall_service.py`：收窄 `_RECALL_INSTRUCTION_CLAUSE_RE`（移除 基于/列出/参考，保留带实体子句）。
- [x] 3.2 `recall_service.py`：`_normalize_recall_query` 全部子句被剔除返回空串。
- [x] 3.3 `recall_service.py`：`recall` operational-only（空 normalized + 无 subject/task_intent）跳过 semantic recall，记 `memory.recall.query_skipped_operational`，返回空。
- [x] 3.4 `test_recall_query_normalization.py`：更新（保留实体、空串、operational-only）。
- [x] 3.5 `test_recall_time_decay.py`：operational-only 跳过 semantic recall（不调 embedding、返回空）用例。

## 4. P1：Candidate 原子化 + Assistant 回声 prompt 强化（§2/§3）

- [x] 4.1 `backends.py` `_system_prompt`：补「不要抽取 Assistant 对 tool/document 原始事实的摘要复述」+ operational 非 research_preference。

## 5. P2：Recall 耗时观测（§8）

- [x] 5.1 `recall_service.py`：`_traced_result` 的 `recall.completed` 增 `accounted_duration_ms`/`unaccounted_duration_ms`。

## 6. 文档与验收

- [x] 6.1 `docs/logging.md`：relation_extraction_attempt/relation_validation_rejected/query_skipped_operational + recall.completed accounted/unaccounted。
- [x] 6.2 创建 `openspec/changes/fix-relation-reliability-operational-query/`。
- [x] 6.3 `openspec-cn validate fix-relation-reliability-operational-query --strict` 通过。
- [x] 6.4 `uv run ruff check .` 通过。
- [x] 6.5 `uv run pyright`（改动文件）0 错误。
- [x] 6.6 `uv run pytest tests/contract/test_dependency_boundaries.py` 通过。
- [x] 6.7 `uv run pytest -q` 通过（312 passed, 13 skipped，含 relation 重试/operational 丢弃/查询跳过用例）。
- [ ] 6.8 真实联调：Case A 原子化、Case B operational 丢弃、Case C 回声不产 Pending、Case D uncertain->pending、Case E relation 重试、Case F relation_accepted>0+timeline hop_count>0、Case G 正向 Recall result_count>0/负向 0（人工验收，需真实环境）。
