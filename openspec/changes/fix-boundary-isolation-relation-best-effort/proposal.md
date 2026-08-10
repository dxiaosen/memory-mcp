## Why

2026-08-09 E2E 日志暴露 4 个边界问题：RelationExtractor 失败回滚合法 Candidate、单条 Candidate 字段错误
拖垮整批、revoke_memory 与 Active Relation 级联触发 CheckViolation、Claude 把普通业务更新当 mutation
命令调用。日志证据：3 次 `memory.capture.incomplete`（Relation 失败所致）、4 次
`memory_relations_terminal_state` CheckViolation（stale_at < relation.created_at）、20 次 Claude 主动
`revoke_memory`/`link_memories`。

## What Changes

- **Relation 降级为 best-effort（§3/§3.5）**：`AutomaticRelationPlanner.plan` 重试耗尽 / 模型结构错误 /
  fatal validation 失败时不再 `raise InvalidModelOutputError`，改为记
  `memory.capture.relation_extraction_failed`（`candidate_persistence_preserved=true`）并返回空 plan；
  `CaptureService` commit 时若 Relation 写入失败则记 `memory.capture.relation_commit_failed`、放弃
  relation 重试 commit（Candidate 主链保留）。Relation 不再参与 Capture 原子边界。
- **Candidate-level discard（§4）**：`candidate_processing.process` 对 `validate_memory_type` /
  `validate_business_progress` 抛错的单条 Candidate 改为 discard（`invalid_memory_type`/
  `invalid_business_progress`/`invalid_candidate_field`），不再让整轮失败。
- **revoke 级联 stale_at 修复（§5）**：`revoke` 的 `stale_at` 由 `revision_created_at` 改为
  `datetime.now(UTC)`，避免关系在 revision 之后建立时 `stale_at < relation.created_at` 违反
  `memory_relations_terminal_state` CHECK 约束。InMemory 适配器同步修正。
- **mutation 边界提示（§6/§7/§8）**：`revoke_memory`/`link_memories`/`confirm_pending_memory`/
  `reject_pending_memory`/`batch_confirm_pending` 的 tool description 增加统一提示--仅用户显式要求
  管理存储记录时调用，普通业务语义由 AfterRun 处理。`CLAUDE.md` 增「业务更新不是记忆管理命令」段。

## Capabilities

### New Capabilities

- `boundary-isolation-relation-best-effort`：规定 Relation best-effort（不回滚 Candidate）、
  Candidate-level discard（单条字段错误不拖垮整批）、revoke 级联 stale_at 修复、mutation 工具边界提示。

### Modified Capabilities

无。

## Impact

- Core：`automatic_relations.py` plan 降级 + `_best_effort_failure_plan`；`candidate_processing.py`
  validate try/except discard；`capture_service.py` commit best-effort 重试；`postgresql/repository.py`
  与 `in_memory.py` revoke `stale_at=now()`。
- Tools：`tools/memory.py` + `tools/review.py` tool description 边界提示。
- 文档：`CLAUDE.md` 边界段；`docs/logging.md` 增 `relation_extraction_failed`/`relation_commit_failed`。
- 不改 MCP DTO、Admission/Relation/Recall 阈值、DB schema、Core 自包含不变量、Prompt 大框架。
