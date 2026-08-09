## Why

2026-08-09 最新多轮 E2E 日志（`.memory-mcp/logs/memory-mcp.log`）暴露 4 个问题：Relation 阶段
不稳定导致整轮 Capture 失败、operational instruction 被存为 research_preference、Recall 查询过度
裁剪丢实体、Recall 总耗时缺口不可观测。

1. **Relation 失败拖垮 Capture**：日志 12 次 `relation source_expression must occur in the redacted
   source turn` -> `InvalidModelOutputError` -> 整轮 `memory.capture.incomplete`（invalid_candidate_output）。
   `automatic_relations.py` 用精确 `not in` 校验 relation source_expression（换行/空格差异即失败），
   且 `plan` 无重试（CandidateExtractor 已有 max_attempts=3，Relation 没有），日志看不到被拒 proposal。
2. **operational instruction 误存**：`不要使用内置的记忆工具` 被抽成 `research_preference`/`user_view`/
   `0.95` -> auto_save。属操作指令，非投研长期偏好。
3. **Recall 查询过度裁剪**：归一化把 `启明先进材料/公司跟踪/研究判断` 等实体裁掉（因「请基于/列出」
   误删带实体子句），却保留 `1. 模拟披露事实` 等指令派生列表项；`不要使用任何内置工具` 回退原文后
   仍发 embedding（浪费）。
4. **Recall 耗时缺口**：`recall.completed.duration_ms` 明显大于各 stage duration 之和，缺口（连接/
   序列化）不可观测。

## What Changes

- **Relation 有界重试 + 三级校验 + rejected 日志**：`plan` 把 extract+admit 包进有界重试
  （`_RELATION_EXTRACTION_MAX_ATTEMPTS=3`），记 `relation_extraction_attempt.started/failed/completed`；
  admit 改为收集 `rejected`（不就地 raise），记 `relation_validation_rejected` content 事件；relation
  source_expression 校验改为三级（raw -> whitespace -> compact）；全部 attempt 均有 rejected 才
  `raise InvalidModelOutputError` -> capture incomplete（保留原子失败安全语义）。
- **operational_instruction 丢弃**：candidate 处理对操作指令（不要使用工具/读取文件/联网等）且未显式
  跨会话持久的候选 discard `operational_instruction`（类型无关，不硬编码 research_preference）。
- **Recall 查询归一化修复**：收窄指令模式（移除「基于/列出/参考」，保留带实体子句）；全部子句被剔除
  返回空串；operational-only 查询跳过 semantic recall（不调 embedding，返回空）。
- **Recall 耗时观测**：`recall.completed` 增 `accounted_duration_ms`/`unaccounted_duration_ms`。
- prompt 强化：原子化、Assistant 回声/工具复述排除、operational instruction 非 research_preference。

## Capabilities

### New Capabilities

- `relation-reliability-operational-query`：规定 Relation 抽取有界重试与 source_expression 三级校验、
  operational instruction 丢弃、Recall 查询归一化（保留实体 + operational-only 跳过）、Recall 耗时观测。

### Modified Capabilities

无。

## Impact

- Core：`automatic_relations.py` 重试 + 三级校验 + `RejectedRelation` + `_admit` 返回 rejected；
  `candidate_processing.py` `_is_operational_instruction` discard；`recall_service.py` 收窄归一化 +
  operational-only 跳过 + accounted/unaccounted duration。
- 提取层：`backends.py` `_system_prompt` 原子化/回声/operational 强化。
- 测试：fakes.py `FakeRelationExtractor` 可配置异常；capture_service 增 relation 重试/operational 丢弃
  用例；recall 增 operational-only 跳过用例；recall_query_normalization 更新。
- 文档：logging.md 增 relation_extraction_attempt/relation_validation_rejected/query_skipped_operational
  事件 + recall.completed accounted/unaccounted。
- 不改对外 DTO、Admission 保守原则、Relation `confidence>=0.90`、DB schema、Core 自包含不变量；
  不让 Pending 端点建关系；不把非法 Relation 改静默忽略；不增 LLM；不在 Core 硬编码 research_preference。
