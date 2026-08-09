## Why

2026-08-09 E2E 日志显示：RelationExtractor 产生 `relation_policy_mismatch`（非致命策略拒绝）后，
系统 retry 3 次再 `memory.capture.incomplete`，整轮 Capture 原子回滚--本来已正确完成的
Candidate / Pending 全部丢失。日志中 9 次 `relation_policy_mismatch` 全部触发 retry->incomplete。

这违反项目优先级「Memory 主链 > Relation 增强能力」：普通 Relation skip 不应拖垮 Capture。
同时 exception message 硬编码为 `relation source_expression must occur in the redacted source turn`，
与真实 reason（policy_mismatch）不一致，调试误导。

## What Changes

- **Relation 校验分级（§2）**：`_admit` 把拒绝分为 **fatal**（retry / 可使 Capture 失败：
  `invalid_source_expression`、`relation_endpoint_outside_catalog`、模型结构错误）与 **non-fatal**
  （直接 skip、Capture 继续、不 retry：`relation_policy_mismatch`、`relation_low_confidence`、
  `relation_not_explicit`、`relation_insufficient_evidence`、`relation_negated`、
  `relation_reversed_direction`、`relation_duplicate`、`relation_non_user_source`）。
- **retry 只用于 fatal（§5）**：`plan` 仅当某次 attempt 有 fatal rejected 才 retry；non-fatal-only
  即完成（`completed`），`skipped_count` 计入 plan，Capture 继续。全部 attempt fatal 才 raise
  `InvalidModelOutputError` -> capture incomplete（保留 fail-closed）。
- **exception message 与 reason 一致（§4）**：raise 消息改为 `relation validation failed: <reason_code>`，
  去掉硬编码过时信息；`relation_validation_rejected` content 事件记全部被拒/跳过 proposal 的真实 reason_code。
- **Relation 自动保存阈值不变（§3）**：`confidence >= 0.90` 保持；低置信度 -> skip（非 accept、非 fail）。
- **prompt 小收紧（§6/§7）**：原子化补「跨时期/跨行/跨 bullet 拆分」；回声排除补「review/timeline status、
  缺失记忆解释」。不新增架构组件。

## Capabilities

### New Capabilities

- `relation-validation-grading`：规定 Relation 校验 fatal/non-fatal 分级与重试决策、exception/reason
  一致性、自动保存阈值不变、以及配套 prompt 收紧。

### Modified Capabilities

无。

## Impact

- Core：`automatic_relations.py` `_admit` 返回 `(accepted, skipped, fatal_rejected)`；新增
  `_relation_skip_reason`（non-fatal reason_code）；`plan` 仅对 fatal rejected retry，non-fatal-only
  即完成；raise 消息与 reason 一致。
- 提取层：`backends.py` `_system_prompt` 原子化（跨时期）+ 回声排除（review/timeline status）。
- 测试：capture_service 增 policy_mismatch/低置信度 non-fatal skip 用例；更新 reversed-direction
  用例（skip 而非 fail）。
- 文档：logging.md `relation_validation_rejected`/`relation_extraction_attempt.*` 更新 fatal/non-fatal
  语义与 reason_code 列表。
- 不改对外 DTO、Admission 保守原则、Relation `confidence>=0.90`、DB schema、Core 自包含不变量；
  不让 Pending 端点建关系；不增 LLM；不新增架构组件。
