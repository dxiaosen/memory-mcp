# Memory MCP 本轮联调修复任务（三）

请基于最新多轮 E2E 日志继续做小范围修复。不要重构整体架构，不修改 Admission / Lifecycle 的核心原则。

本轮优先级：

1. 修复已知的 `source_expression` 中文换行问题；
2. 修复 Candidate 原子化 / Evidence 覆盖；
3. 新增结构化抽取失败重试；
4. 减少 Assistant 回声和元信息造成的 Pending 污染；
5. 优化主动 Recall 查询构造；
6. Relation Core 暂不贸然修改，先做前置修复后的回归验证。

## 1. P0：修复 `source_expression` 中文换行 / 空白误杀

当前用户明确陈述仍大量被 `invalid_source_expression` 拒绝。

典型情况：

```text
原文：
AI 热管理材料收入快速增长、收入占比提升和较高毛利率，
可能推动公司收入与利润结构改善。

source_expression：
AI 热管理材料收入快速增长、收入占比提升和较高毛利率，可能推动公司收入与利润结构改善。
```

这类只存在换行 / 空白差异，应通过校验。

建议两级校验：

```python
def normalize_whitespace(text: str) -> str:
    return " ".join(text.split())

def normalize_compact(text: str) -> str:
    return "".join(text.split())
```

先 `normalize_whitespace` containment；失败后再用 `normalize_compact` containment。

只忽略 Unicode whitespace，不做语义模糊匹配，不修改标点、数字或字符，不允许模型自行拼接多个独立 bullet。

要求：

```text
纯换行 / 空格差异
→ valid

模型改写、增加标点、拼接多个独立证据
→ invalid_source_expression
```

补测试覆盖中文逗号后换行、句中换行、连续空格、CRLF/LF，以及多个 bullet 被模型用 `；` 拼接时仍拒绝。

## 2. P0/P1：加强 Candidate 原子化与 Evidence Coverage

当前仍有一个 `source_expression` 只支撑 Candidate 一部分，但 Candidate content 汇总多条事实的情况。

例如：

```text
source_expression:
营业收入 | 3,120 | 4,260 | 36.5%

content:
营业收入 + 净利润 + 毛利率 + 资本开支
```

请加强 Candidate Extraction Prompt：

- 一个 Candidate 只能表达一条原子记忆；
- external_fact / user_provided_fact 的关键事实和数字必须由 `source_expression` 完整支撑；
- 若需要多个表格行、多个 bullet、多个来源，优先拆成多个 Candidate；
- 不得用局部 `source_expression` 支撑整段汇总内容；
- 不要把事实、研究判断和关系语义混在一个 Candidate 中。

例如：

```text
2026Q1 AI收入同比 +67.7%，支持某 thesis
```

应拆为：

```text
evidence_claim:
2026Q1 AI收入同比 +67.7%
```

以及独立关系：

```text
supports:
evidence_claim -> thesis
```

不要把 `supports/challenges/threatens/could_catalyze/addresses` 写进事实 Candidate content。

暂时不要新增复杂多 Evidence 模型。

## 3. P0：CandidateExtractor 结构化输出失败时有界重试

日志出现：

```text
memory.capture.invalid_output
InvalidModelOutputError

CandidateBatch:
input_value=None
input_type=NoneType
```

随后整个 Capture：

```text
memory.capture.incomplete
failure_code=invalid_candidate_output
```

且没有自动重试。

请在 Server 侧 Candidate Extraction 内增加有界重试，建议 `max_attempts=2` 或 `3`。

仅对可恢复的模型结构错误重试：

- structured output 为 null / None；
- JSON/schema parse 失败；
- CandidateBatch validation failure；
- 模型返回非预期结构。

不要对业务校验结果如 `invalid_source_expression` 重试整个 Extraction。

建议日志：

```text
memory.capture.extraction_attempt.started
memory.capture.extraction_attempt.failed
memory.capture.extraction_attempt.completed
```

字段至少：

```text
capture_id
attempt
max_attempts
duration_ms
error_type
retryable
```

只有所有 attempt 都失败后，才写：

```text
memory.capture.incomplete
failure_code=invalid_candidate_output
```

要求重试发生在同一个 Capture 内，不产生重复 Capture / Memory。

## 4. P1：抑制 Assistant 回声造成的重复 Candidate / Review 污染

当前 Recall 后 Assistant 会复述已有 Memory，例如：

```text
Q1 二期产线良率仅 78%
```

AfterRun 又将其抽成：

```text
evidence_claim
source_role=assistant
decision=pending
reason=non_user_source
```

形成：

```text
Memory A 被 Recall
→ Assistant 复述 A
→ Capture 再抽 A
→ 新 Pending
```

此外，Timeline 只是查看图谱状态，但 Assistant 描述“应存在的 thesis / support / catalyst / research question”后，又被抽出了 8 条 Pending。

请在 Extraction Prompt 中明确不要把以下内容当新长期记忆：

- Assistant 对已召回 Memory 的简单复述；
- Assistant 对 memory system 当前状态的描述；
- “当前缺少某节点 / 应存在某节点 / 尚未写入图谱”等元信息；
- 为解释工具结果临时生成的 memory topology；
- 未经用户明确采纳的 Assistant 分析框架。

同时增加保护：

```text
source_role=assistant
且与已有 active memory 高度重复
→ discard
reason_code=assistant_restatement
```

不要为其新建 Pending，也不要把 Assistant 回声当成已有 Memory 的新 Evidence。

用户本人再次明确陈述同一内容时，仍按现有 duplicate/evidence 规则处理。

## 5. P1：限制 `research_preference` 的来源语义

当前出现 Assistant 自己提出的分析框架被抽成：

```text
memory_type=research_preference
source_role=assistant
assertion_kind=system_inference
```

`research_preference` 应表示用户长期偏好。

请要求它优先满足：

```text
source_role=user
expression_basis=explicit
```

Assistant 建议的分析框架若用户没有明确采纳，不应标为 `research_preference`。

## 6. P1：继续落实 Source Priority，尤其 external_fact

同一个 CompletedTurnEvent 中已经有原始 tool/document 数据，但部分精确财务事实仍绑定到 Assistant 总结。

对于事实型 Candidate，优先级保持：

```text
user explicit statement
>
tool/document original evidence
>
assistant paraphrase
```

规则：

```text
用户明确给出事实
→ source_role=user
→ assertion_kind=user_provided_fact

用户未给出精确值，但 Tool/Document 有原始值
→ source_role=tool
→ assertion_kind=external_fact

只有 Assistant 推导/计算
→ source_role=assistant
→ assertion_kind=system_inference
```

不要因为 Assistant 总结更完整就覆盖同 Turn 中更可信的 Tool 原始来源。

## 7. P1：优化 BeforeRun 主动 Recall 查询，不要直接降低阈值

自然用户请求：

```text
我要准备启明先进材料下一次财报跟踪，
请基于此前研究判断……
```

日志中同一 Memory：

```text
candidate_count=1
score≈0.155
threshold_passed_count=0
result_count=0
```

Agent 随后用更短查询：

```text
启明先进材料 研究判断 风险 财报跟踪
```

同一 Memory：

```text
score≈0.291
threshold_passed_count=1
result_count=1
```

说明直接使用完整用户 Prompt 时，操作指令和格式要求会稀释 Recall query。

同时负向测试：

```text
南美铜矿公司 矿山寿命
```

虽然 vector candidate 碰到启明先进材料 Memory，但：

```text
score≈0.129
threshold_passed_count=0
result_count=0
```

这个负向过滤是正确的。

因此不要简单下调全局 threshold。

优先做轻量确定性 Query normalization：

```text
raw prompt
→ 去除明显操作/工具/格式指令
→ 保留实体、主题、研究任务关键词
→ recall query
```

例如去掉：

```text
不要读取项目文件
不要使用内置工具
按某格式输出
```

保留：

```text
启明先进材料
下一次财报跟踪
此前研究判断
风险
```

如果已有 `subject` / `task_intent`，应加入 Recall Query 或结构化 boost。

要求：

- 不增加额外 LLM 调用；
- 不改变 owner/profile/lifecycle 过滤；
- 正向自然语言查询第一次 BeforeRun Recall 就成功；
- 南美铜矿负向测试仍为 0。

## 8. Relation / Timeline：先不要改 Relation Core

用户已明确表达：

```text
supports
challenges
threatens
could_catalyze
addresses
```

但当前：

```text
relation_proposal_count=0
relation_accepted_count=0
timeline hop_count=0
```

现阶段不一定是 Relation Core Bug。

关系生成只允许 active/current/effective 且 auto-saved 的端点参与，而当前大量 thesis / evidence / catalyst / research_question 因 `invalid_source_expression` 被 discard，实际端点不完整。

所以先完成第 1、2 项，然后重新执行关系案例。

回归案例应准备 active 节点：

```text
thesis
evidence_claim
risk
catalyst
research_question
ongoing_research
```

用户明确给关系：

```text
evidence supports thesis
risk threatens thesis
catalyst could_catalyze thesis
ongoing_research addresses research_question
```

预期：

```text
relation_proposal_count > 0
relation_accepted_count > 0
```

随后：

```text
recall_memory(mode=timeline, focus_memory_id=thesis_id)
```

应：

```text
hop_count > 0
```

如果前置问题修好后仍为 0，再单独排查 Relation Extraction。

## 9. P2：Recall Repository 性能后续优化

只有少量 active Memory 时，多次出现：

```text
repository_candidate_duration_ms ≈ 1100～2800ms
evidence_loading_duration_ms ≈ 200～1300ms
```

功能稳定后再排查：

- PostgreSQL 连接池；
- 是否每次 Recall 重复建连；
- lexical/vector/recent 是否串行查询；
- SQL index / query plan；
- relation/evidence N+1；
- 远程 DB RTT。

暂时不要为了性能重构 Recall 算法。

# 当前已验证正确，不要回退

- CompletedTurnEvent 只包含当前 Turn；
- Tool messages 不跨 Turn 重复；
- workspace-relative document URI；
- user explicit 能识别为 `user_view` / `user_provided_fact`；
- explicit durable user statement 能 auto-save；
- active Memory 可以成功 Recall；
- 南美铜矿负向查询最终不会注入启明先进材料 Memory；
- Timeline 没有 Relation 时正确返回 `hop_count=0`；
- Capture / Recall 分阶段耗时日志；
- Candidate 数量统计；
- validation rejected 完整日志；
- zero-result `rendered_context=""`；
- timeout/replay 修复。

# 验收顺序

## Case A：用户长期研究基准

重新提交跨行中文长期判断。

预期纯 whitespace/newline 不再触发 `invalid_source_expression`，并至少能保存 thesis / risk / ongoing_research / research_preference。

## Case B：主动 Recall 正向测试

新会话直接输入自然语言：

```text
我要准备启明先进材料下一次财报跟踪，
请基于此前研究判断列出最值得验证的问题。
```

不依赖 Agent 二次手工调用 MCP。

第一次 BeforeRun Recall 即：

```text
result_count > 0
rendered_context != ""
```

## Case C：主动 Recall 负向测试

```text
回顾南美铜矿公司的矿山寿命判断
```

预期：

```text
result_count=0
rendered_context=""
```

## Case D：结构化模型瞬时失败

Mock CandidateExtractor 第一次返回 None / invalid JSON，第二次返回合法 CandidateBatch。

预期：

```text
capture completed
extraction_attempt_count=2
```

且只写一份最终结果。

## Case E：Assistant 回声

先 Recall 一条 Memory，让 Assistant 在答案中复述。

AfterRun 不应产生新的重复 Pending。

预期：

```text
assistant_restatement
→ discard / ignored
```

## Case F：Relation + Timeline

确保各关系端点均为 active，再提交明确关系。

预期：

```text
relation_accepted_count > 0
```

随后 timeline：

```text
hop_count > 0
```

# 约束

- 不修改 Admission 的保守原则；
- 不允许 Assistant 推断自动变成 user_view；
- 不降低 sensitive / blocked 规则；
- 不让 Pending 端点自动建关系；
- 不新增复杂 salience pruning；
- 不新增 LLM 调用构造 Recall query；
- 保持 MCP DTO 向后兼容；
- 优先小改动；
- 每项修复补单元测试；
- 最后补完整跨会话 E2E：Capture → Auto-save → Recall → Relation → Timeline。