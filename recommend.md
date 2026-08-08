# Memory MCP 本轮修复任务

请基于当前实现做小范围修复，不要重构整体架构，也不要修改现有 Admission / Lifecycle 核心规则。

## 1. 修正 Candidate 计数语义

当前出现：

- candidate_count=11
- pending_count=11
- discarded_count=1
- 实际 outcome_count=12

请统一计数语义。

建议明确区分：

- `extracted_candidate_count`：模型原始抽取数量
- `validated_candidate_count`：通过前置校验的数量
- `outcome_count`：最终产生 decision 的数量
- `pending_count`
- `discarded_count`

要求各统计字段能够互相对上。

同时，开发日志中要能看到被 `invalid_source_expression` 等原因提前拒绝的完整 Candidate，便于调试。

## 2. 加强 Candidate 原子化和 Evidence 覆盖

一个 Candidate 不要混合：

- 外部事实
- 计算结果
- 研究判断
- 推断

例如：

“2025 Capex 720、2026计划900，因此投资回报验证窗口在2026H2-2027”

应该拆成：

1. `evidence_claim + external_fact`
   - 2025 Capex 720
   - 2026计划900

2. `risk/thesis + system_inference`
   - 高资本开支导致产能回报仍需后续验证

同时要求：

`source_expression / Evidence` 必须能够支撑 Candidate 的完整 content。

如果一个 Candidate 依赖多个来源，应允许绑定多个 Evidence，而不是用一条 source_expression 支撑整段内容。

## 3. 保证 assertion_kind 与 expression_basis 一致

避免出现：

```text
assertion_kind=external_fact
expression_basis=inferred
```

建议规则：

- 明确来自 document/tool 的原始事实
  → `external_fact + explicit`

- Assistant 基于多个事实形成的分析、风险、thesis、research question
  → `system_inference + inferred`

不要把事实和推断塞进同一个 Candidate。

## 4. source_uri 改成 workspace-relative

当前：

```text
C:\Users\Sen\Desktop\user-1\materials\02_2025年报摘要.md
```

改为类似：

```text
materials/02_2025年报摘要.md
```

或者：

```text
workspace://materials/02_2025年报摘要.md
```

避免长期记忆绑定某一台电脑的绝对路径。

Host Adapter 负责把真实绝对路径转换为 workspace-relative URI。

## 5. 增加 Capture 分阶段耗时

参考 Recall 当前已有的分阶段耗时，给 Capture 增加：

- `candidate_extraction_duration_ms`
- `candidate_validation_duration_ms`
- `admission_duration_ms`
- `lifecycle_duration_ms`
- `relation_duration_ms`
- `persistence_duration_ms`

最终：

```text
memory.capture.completed
```

中保留总 duration，同时输出这些阶段耗时，方便定位当前约 25 秒 Capture 的主要耗时来源。

## 6. 调整开发日志事件顺序

日志应尽量反映真实业务执行顺序：

```text
memory.capture.started
memory.capture.input
memory.capture.candidates
memory.capture.validation
memory.capture.admission
memory.capture.relations_planned
memory.capture.relation_candidates
memory.capture.persisted
memory.capture.completed
```

如果当前实际执行顺序不是这样，请先确认业务逻辑，不要只为了日志顺序移动代码。

## 约束

- 不修改当前开发阶段的完整内容日志策略。
- 不做日志脱敏调整。
- 不修改现有 Profile / Admission 的主要决策规则。
- 不新增复杂 salience pruning 机制。
- 保持现有 MCP 接口和 DTO 向后兼容。
- 优先小改动，并补充对应单元测试。