## Why

2026-08-07 20:25 第二轮真实联调日志暴露 6 个可观测性与语义问题，影响调试与计数
准确性：

1. **计数语义不清**：`candidate_count=11`、`pending_count=11`、`discarded_count=1`，
   实际 `outcome=12`。`candidate_count` 只含通过校验的，不含 discard，但字段名让人
   以为它等于 outcome 总数；被 `invalid_source_expression` 拒绝的候选无完整内容可调试。
2. **候选混合事实与推断**：模型把"2025 Capex 720、2026 计划 900，因此回报窗口在
   H2"塞进一条候选，事实与推断混淆。
3. **assertion_kind 与 expression_basis 不一致**：出现 `external_fact+inferred`
   （资本开支强度），文档来源的推断被标成原始事实。
4. **source_uri 为绝对路径**：长期记忆绑定某一台机器的绝对路径，迁移即失效。
5. **Capture 无分阶段耗时**：25s capture 不知道慢在抽取/校验/准入/lifecycle/关系/持久化哪段。
6. **日志事件顺序与业务不符**：`relations_planned` 先于 `candidates`/`admission` 输出，
   但业务上候选处理先于关系规划。

## What Changes

- **计数语义**：`memory.capture.completed` 新增 `extracted_candidate_count`（模型原始抽取）
  与 `outcome_count`（产生 decision 的总数）；`candidate_count` 保留为通过校验的数量。
  恒等式：`outcome_count == auto_saved + pending + discarded + blocked`。
- **被拒候选可调试**：新增 `memory.capture.validation` 内容事件，记录被前置校验拒绝的
  proposal 完整字段（subject/content/source_expression/assertion_kind/expression_basis + reason）。
- **原子化引导**：extraction prompt 强调一候选一事实/一推断，混合事实与结论需拆分；
  source_expression 必须完整支撑 content，跨来源拆分多候选。
- **assertion_kind 与 expression_basis 一致**：`_normalize_assertion_kind` 增 `expression_basis`
  参数--document/tool/web + inferred -> system_inference（推断非原始事实）；
  + explicit + 非外部 -> external_fact。prompt 补"external_fact 配 explicit，
  system_inference 配 inferred"。
- **source_uri workspace-relative**：`extract_document_messages` 增 `cwd` 参数，用
  `os.path.relpath` 转相对路径；Host Adapter 传 `event.cwd`。无 cwd 保留原路径。
- **Capture 分阶段耗时**：`memory.capture.completed` 新增 6 个 `*_duration_ms`
  （extraction/validation/admission/lifecycle/relation/persistence），`CandidateProcessingResult`
  内部加 `timing` dict 透传 process 内三段耗时。
- **日志事件顺序**：调整 log 语句顺序为 candidates -> validation -> admission ->
  relations_planned -> relation_candidates -> persisted -> completed，对齐业务执行顺序。

## Capabilities

### New Capabilities

- `capture-observability`：规定候选计数语义、被拒候选可观测、Capture 分阶段耗时、
  日志事件顺序、以及 assertion_kind 与 expression_basis 一致性归一化。

### Modified Capabilities

无。本变更以独立增量能力描述新增行为。

## Impact

- Core：`candidate_processing.py` 增 `RejectedProposal` 与 `CandidateProcessingResult.timing`；
  `process` 内部按 validation/admission/lifecycle 累加耗时；`_normalize_assertion_kind` 增
  expression_basis 规则。`capture_service.py` 计时 extraction/relation/persistence、
  调整日志顺序、新增 validation 事件与计数字段。`recall_service.py` 无改动。
- 提取层：`backends.py` `_system_prompt` 增原子化与 expression_basis 一致性指导。
- Agent：`transcript.py` `extract_document_messages` 增 cwd；`hosts.py` 透传 cwd。
- 文档：logging.md 更新 completed 字段表 + assertion_normalized 说明 + validation 事件行。
- 测试：capture_service 增计数语义 + document+inferred 归一化用例；capture_adapters 无改动；
  agent_transcript 增 workspace-relative 用例；logging_events 增计数 + 阶段耗时断言。
- 不改对外 DTO（Candidate/Evidence/CaptureOutcome 字段不变）、不改 Admission/Lifecycle
  决策规则、不改 DB schema、不改 Core 自包含不变量。
