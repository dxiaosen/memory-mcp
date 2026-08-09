# Memory MCP 下一轮修复方案

基于最新多轮 E2E 日志，当前核心 Memory Capture / Auto-save / Recall 已基本跑通。下一轮不建议再改整体架构，重点解决 Relation 阶段稳定性、Candidate 原子化、Assistant 回声污染和 operational instruction 误存为 research_preference 的问题。

本轮优先级：

1. P0：RelationExtractor 的 `source_expression` 校验与重试
2. P1：Candidate 原子化与 Evidence Coverage
3. P1：抑制 Assistant / memory-system 回声污染
4. P1：限制 `research_preference` 的来源语义
5. P1：继续优化 Recall query normalization
6. P2：补充性能观测

---

## 1. P0：RelationExtractor 增加有界重试

最新日志多次出现：

```text
relation source_expression must occur in the redacted source turn
```

随后整个 Capture：

```text
memory.capture.incomplete
failure_code=invalid_candidate_output
```

当前这种“非法 Relation 导致整个 Capture 原子失败”的安全语义可以保留，不要改成静默忽略。

需要修的是：

- RelationExtractor 输出不稳定；
- CandidateExtractor 已有 `max_attempts=3`，RelationExtractor 还缺少同等级别的 retry；
- 日志看不到具体 rejected relation proposal，调试信息不足。

### 目标

Relation extraction 使用：

```text
attempt 1
→ InvalidModelOutputError
→ retry

attempt 2
→ success
→ continue Capture
```

只有所有 attempt 都失败时才：

```text
memory.capture.incomplete
failure_code=invalid_candidate_output
```

### 建议日志

新增：

```text
memory.capture.relation_extraction_attempt.started
memory.capture.relation_extraction_attempt.failed
memory.capture.relation_extraction_attempt.completed
```

字段至少：

```text
capture_id
attempt
max_attempts
duration_ms
error_type
error_message
retryable
```

开发 content log 下额外打印：

```text
memory.capture.relation_validation_rejected
```

包含：

```text
source_memory_id
target_memory_id
relation_type
confidence
source_expression
reason_code
```

### `source_expression` 校验

Relation 的 `source_expression` 建议复用 Candidate 当前的 whitespace normalization：

```python
def normalize_whitespace(text: str) -> str:
    return " ".join(text.split())

def normalize_compact(text: str) -> str:
    return "".join(text.split())
```

验证顺序：

```text
1. raw containment
2. normalize_whitespace containment
3. normalize_compact containment
```

只忽略 whitespace。

不要允许：

- 改标点；
- 改数字；
- 拼接多个独立句子；
- 使用 Assistant 改写后的关系句冒充用户原文。

---

## 2. P1：修复 Candidate 原子化 / Evidence Coverage

当前依然存在：

```text
source_expression:
营业收入 | 3,120 | 4,260 | 36.5%

content:
营业收入 + 净利润 + 毛利率 + 经营现金流 + 资本开支
```

以及：

```text
source_expression:
AI 热管理材料一行

content:
AI / 新能源汽车 / 工业复合材料三个分部汇总
```

这说明 source existence validation 已通过，但 Evidence Coverage 仍不完整。

### 规则

对于：

```text
external_fact
user_provided_fact
```

要求：

> Candidate content 中每个关键事实 / 数字必须由同一个 `source_expression` 完整支撑。

如果一条 Candidate 需要：

- 多个表格行；
- 多个 bullet；
- 多个文件；
- 事实 + 判断；
- 事实 + relation；

则优先拆分。

### 示例

不要：

```text
2025A 财务：
营收4260、净利润518、毛利率33.2%、OCF390、Capex720
```

只绑定营业收入一行。

应该拆为：

```text
Candidate A
subject=2025A营业收入
content=2025A营业收入4260，同比增长36.5%
source_expression=营业收入行

Candidate B
subject=2025A归母净利润
content=2025A归母净利润518，同比增长56.0%
source_expression=净利润行

Candidate C
subject=2025A毛利率
content=2025A毛利率33.2%
source_expression=毛利率行
```

### Prompt 增加明确规则

```text
One candidate = one atomic memory.

For external_fact / user_provided_fact:
every important fact and number in content must be directly supported
by the selected source_expression.

If one source span cannot support the whole content, split the candidate.
```

暂时不要引入复杂多 Evidence Candidate。

---

## 3. P1：抑制 Assistant 回声和 memory-system 元信息污染

当前存在：

```text
Active Memory
→ Recall
→ Assistant 复述
→ AfterRun Capture
→ 再生成 Pending
```

以及 Assistant 描述：

```text
当前有哪些 Memory
缺哪些节点
应存在什么 Relation
Timeline 当前为空
```

后又被 CandidateExtractor 当成长期研究内容。

这会造成 review 数量持续膨胀。

### Extraction Prompt 增加排除规则

不要提取：

- Assistant 对已召回 Memory 的简单复述；
- Assistant 对 Tool / Document 原始事实的摘要性重述；
- memory system 当前状态；
- “目前只有 X 条 Memory”；
- “当前缺少 thesis / catalyst / relation”等系统元信息；
- Timeline / Review / Memory topology 的解释；
- 未经用户明确采纳的 Assistant 临时分析框架。

### pre-admission / dedup 保护

若：

```text
source_role=assistant
```

并且 Candidate 与已有 active memory 高度重复：

```text
decision=discard
reason_code=assistant_restatement
```

不要：

- 新建 Pending；
- 给已有 Memory 增加 Assistant Evidence；
- 把 Assistant 回声当成用户确认。

用户本人再次明确表达相同观点时，仍按现有 duplicate/evidence 规则处理。

---

## 4. P1：`research_preference` 只表示用户长期偏好

当前出现：

```text
用户：
不要使用内置的记忆工具
```

被保存为：

```text
memory_type=research_preference
auto_save
```

这不适合作为投资研究长期偏好。

### 新规则

`research_preference` 应主要用于：

```text
以后分析公司时使用中文
关键数字用表格
按事实—判断—风险—验证指标输出
管理层口径标记待验证
```

也就是：

- 用户明确；
- 持久；
- 与研究工作方式相关；
- 可跨任务复用。

而下面这些属于 operational instruction：

```text
不要读取项目文件
不要调用内置工具
不要使用内置 memory
本轮不要联网
本轮不要打开某文件
```

默认：

```text
durability=temporary
→ discard
```

除非用户显式表达：

```text
以后所有会话都……
今后始终……
长期默认……
```

并且该设置确实是跨会话稳定偏好。

建议增加：

```text
reason_code=operational_instruction
```

用于开发日志和统计。

---

## 5. P1：继续优化 Recall Query Normalization

现在 query normalization 已经能去掉部分工具/格式指令，这是正确方向。

但存在过度裁剪。

例如原始请求包含：

```text
启明先进材料
公司跟踪
研究判断
```

normalized query 最后只剩：

```text
模拟披露事实
管理层口径
研究判断
待验证假设
```

真正有区分度的实体反而丢了。

### 目标

优先保留：

- 公司 / 人 / 项目 / 产品等实体；
- 主题；
- 研究任务；
- 风险 / thesis / catalyst / tracking 等业务词。

去掉：

- “不要读取项目文件”；
- “不要使用某工具”；
- 文件路径列表；
- 输出格式要求；
- markdown 格式要求；
- “请简短回答”等低语义指令。

### Operational-only Query

如果 normalization 后只剩：

```text
不要使用工具
不要读取文件
```

这种没有业务检索价值的内容，可以：

```text
skip semantic recall
result_count=0
rendered_context=""
```

避免不必要的 embedding 请求。

### 不要做的事

不要：

- 降低全局 relevance threshold；
- 增加额外 LLM query rewrite；
- 改 owner/profile/lifecycle filter。

---

## 6. Relation E2E 验收方案

Relation Core 当前先不要重构。

先确保以下 active endpoint 已存在：

```text
thesis
evidence_claim
risk
catalyst
research_question
ongoing_research
```

然后用户明确表达：

```text
1. 2026Q1 AI 热管理收入 +67.7%
   supports
   AI 业务结构升级推动收入增长

2. 二期良率78% + 现金流恶化
   challenges
   利润质量已经明显改善

3. 硅树脂和铜箔涨价
   threatens
   毛利率持续提升

4. 液冷材料认证形成正式订单
   could_catalyze
   中期AI增长

5. 持续跟踪良率和现金流
   addresses
   AI增长能否转化为高质量利润
```

### 预期

```text
relation_proposal_count > 0
relation_accepted_count > 0
```

Relation 自动保存仍保持：

```text
confidence >= 0.90
```

不要为通过测试而降低阈值。

随后：

```text
recall_memory(
    mode=timeline,
    focus_memory_id=<thesis>
)
```

预期：

```text
hop_count > 0
```

---

## 7. Pending / Review 流程重新验证

之前“不确定想法”一轮因 RelationExtractor 失败导致整个 Capture 回滚，因此不能视为 Pending 流程通过。

重新测试：

```text
我有几个还不能确定的想法：

1. 海外服务器客户明年可能明显增长；
2. 硅树脂价格下半年可能回落；
3. 新产线也许会提前达到85%良率。

这些目前都只是猜测，还没有足够证据。
```

预期：

```text
source_role=user
durability=uncertain
confidence < 0.9
```

Admission：

```text
decision=pending
```

而不是：

```text
auto_save
discard
```

随后验证：

```text
list_pending_reviews
confirm_pending_memory
reject_pending_memory
```

并确认：

```text
confirm → active Memory
reject  → 不进入 active Memory
```

---

## 8. P2：补充性能观测

某些 Recall：

```text
memory.recall.completed.duration_ms
```

明显大于各 stage duration 之和。

建议增加：

```text
accounted_duration_ms
unaccounted_duration_ms
```

或进一步拆：

```text
query_normalization_duration_ms
repository_connection_duration_ms
relation_loading_duration_ms
serialization_duration_ms
```

当前不要为性能改 Recall 架构。

---

# 当前不应回退的行为

以下已符合预期：

- CompletedTurnEvent 只包含当前 Turn；
- Tool messages 不跨 Turn 重复；
- PostgreSQL pool 正常启动；
- Team extraction 已按真实 team 去重；
- CandidateExtractor 有 `max_attempts=3`；
- zero-result Recall 返回空上下文；
- explicit user statement 可以 auto-save；
- active Memory 可以成功 Recall；
- unrelated query 会被 relevance threshold 拦截；
- workspace-relative document provenance；
- Candidate count / validation / stage duration 日志；
- Pending / auto-save / blocked 的 Admission 原则；
- Relation 自动保存阈值继续保持 `>=0.90`；
- Timeline 没有关系时返回 `hop_count=0`。

---

# 最终验收顺序

## Case A：Candidate Atomicity

输入多行财务表。

预期：

```text
每个 Candidate 的 content
都被其 source_expression 完整支撑
```

不再出现“一条表格行支撑整张财务摘要”。

## Case B：Operational Instruction

输入：

```text
不要使用内置记忆工具
```

预期：

```text
candidate_count=0
```

或：

```text
decision=discard
reason_code=operational_instruction
```

不能 auto-save 为 `research_preference`。

## Case C：Assistant Restatement

先 Recall 一个 active Memory。

Assistant 在回复里复述它。

AfterRun：

```text
不产生新的 Pending
```

## Case D：Uncertain → Pending

用户明确说：

```text
只是猜测
没有足够证据
```

预期：

```text
pending_count > 0
```

且 Capture 成功完成。

## Case E：Relation Retry

Mock：

```text
Relation attempt 1
→ invalid source_expression

Relation attempt 2
→ valid proposal
```

预期：

```text
Capture completed
relation_attempt_count=2
```

不产生重复 Memory。

## Case F：Relation + Timeline

已有完整 active endpoints。

预期：

```text
relation_accepted_count > 0
```

随后：

```text
timeline hop_count > 0
```

## Case G：Recall Query Normalization

正向：

```text
我要继续跟踪启明先进材料，
请基于此前研究判断分析下一次财报。
```

第一次 BeforeRun：

```text
result_count > 0
```

负向：

```text
回顾南美铜矿公司的矿山寿命判断
```

预期：

```text
result_count=0
```

---

# 约束

- 不修改 Admission 的保守原则；
- 不降低 Relation `confidence >= 0.90`；
- 不让 Pending endpoint 自动建关系；
- 不允许 Assistant inference 变成 user_view；
- 不新增复杂 salience pruning；
- 不新增 LLM 调用做 Recall query rewrite；
- 不修改现有 MCP DTO 兼容字段；
- 保持 Capture 原子性；
- 优先小改动；
- 每个问题补对应单元测试；
- 最后补完整 E2E：

```text
User explicit
→ Candidate
→ Auto-save
→ Recall
→ Relation
→ Timeline
```