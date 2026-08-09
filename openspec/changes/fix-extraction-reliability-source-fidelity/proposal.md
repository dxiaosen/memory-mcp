## Why

2026-08-08 最新多轮 E2E 日志暴露 7 个抽取可靠性与来源保真问题，导致用户长期判断被误杀、
抽取瞬时失败整轮丢失、Assistant 回声污染 Pending、Recall 查询被操作指令稀释：

1. **source_expression 仍误杀**：模型把换行**删除**（如「较高毛利率，\n可能」->「较高毛利率，可能」），
   第二轮的单空格归一化后原文为「较高毛利率， 可能」，仍不匹配 -> `invalid_source_expression`。
2. **Candidate 非原子**：一个 `source_expression`（单表格行）支撑汇总多条事实的 content。
3. **抽取失败无重试**：模型返回 null/非法结构 -> `InvalidModelOutputError` -> 整轮
   `invalid_candidate_output`，无有界重试。
4. **Assistant 回声污染**：Recall 后 Assistant 复述已有 Memory -> 再抽成 `source_role=assistant`
   -> 新 Pending，污染 Review。
5. **research_preference 来源错配**：Assistant 自提分析框架被标为 `research_preference`。
6. **external_fact 绑 assistant**：同 Turn 有 tool/document 原始值，但事实仍绑 assistant 汇总。
7. **Recall 查询稀释**：完整用户 Prompt（操作/格式指令、文件列表）稀释 embedding，score<阈值。

## What Changes

- **两级空白归一化**：source_expression 校验先 `normalize_whitespace` containment，失败再
  `normalize_compact`（移除全部空白）containment。只忽略 Unicode 空白，不改标点/数字/字符；
  模型改写/增标点/拼接独立 bullet 仍判 invalid。
- **原子化 prompt**：一个 Candidate 一条原子记忆；external_fact/user_provided_fact 关键事实与数字
  必须由 source_expression 完整支撑；不得把关系语义（supports/challenges/...）写进事实 content。
- **抽取有界重试**：capture 内对 `InvalidModelOutputError` 重试最多 3 次，记
  `extraction_attempt.started/failed/completed` 事件；全失败才写 `incomplete`。
- **Assistant 回声丢弃**：`source_role=assistant` 且与已有 active memory 高度重复（精确命中 +
  content 复述，或语义相似度兜底）-> discard `assistant_restatement`，不建 Pending/Evidence。
- **research_preference 限用户**：prompt 明确 research_preference 仅用户长期偏好
  （source_role=user、explicit）；Assistant 未采纳框架改用 thesis/system_inference。
- **external_fact 来源优先**：prompt 强化事实型优先级 user > tool/document > assistant。
- **Recall 查询归一化**：recall 入口对 query 做确定性归一化（剔除操作/工具/格式指令子句与文件列表行，
  保留实体/主题），用于 embedding+lexical；不下调全局阈值。

## Capabilities

### New Capabilities

- `extraction-reliability-source-fidelity`：规定 source_expression 两级空白校验、Candidate 原子化、
  抽取有界重试、Assistant 回声丢弃、research_preference 用户来源、external_fact 来源优先、
  Recall 查询归一化。

### Modified Capabilities

无。

## Impact

- Core：`candidate_processing.py` 两级归一化 + `_is_assistant_restatement`/`_content_restates`
  回声丢弃；`capture_service.py` 抽取重试 + `_extract_candidates` + attempt 事件；`recall_service.py`
  `_normalize_recall_query` 查询归一化。
- 提取层：`backends.py` `_system_prompt` 原子化/回声/偏好/来源优先引导。
- 测试：fakes.py `FakeCandidateExtractor` 可配置异常；capture_service 增两级归一化/重试/回声用例；
  recall_query_normalization 单测；logging_events 增 extraction_attempt 断言。
- 文档：logging.md 增 extraction_attempt 事件 + recall.input normalized_query 字段。
- 不改对外 DTO、Admission/Lifecycle 主规则、DB schema、Core 自包含不变量、敏感/脱敏策略；
  不增 LLM 调用；Relation Core 本轮不改（前置修复后回归）。
