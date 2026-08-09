Memory MCP 最后一轮修复方案

目标：只修复当前仍会影响主链稳定性的 Relation 错误处理问题，并顺手收紧两个低成本问题。不要再扩展架构，不新增复杂机制。

当前核心链路已经可用：

User explicit
→ Candidate
→ Auto-save
→ PostgreSQL
→ 新会话 Recall
→ Agent 使用历史记忆

因此本轮只做最后收口。

1. P0：Relation 普通校验失败不能拖垮整个 Capture

当前问题：

RelationExtractor 产生 proposal 后，如果出现：

relation_policy_mismatch

系统会：

Relation validation fail
→ retry
→ retry
→ retry
→ memory.capture.incomplete
→ 整个 Capture 原子回滚

这会导致本来已经正确完成的 Candidate / Admission 结果全部丢失。

例如用户明确说：

这些只是猜测，还没有足够证据

Candidate 本来应该进入：

pending

但因为 Relation proposal 不合法，整轮 Capture 被回滚，最终 Pending 也没有保存。

这不符合当前项目的优先级：

Memory 主链 > Relation 增强能力

2. Relation validation 错误分级

请把 Relation validation failure 分为两类。

2.1 Fatal：仍然重试，最终可使 Capture 失败

以下属于真正不可信模型输出：

unknown endpoint memory_id
owner mismatch
profile mismatch
malformed relation schema
relation_type 不存在
source / target endpoint 不存在
明显伪造的 source_expression
source_expression 无法在 source turn 中找到

行为：

attempt 1 fail
→ retry

attempt 2 fail
→ retry

attempt 3 fail
→ Capture failed

保留当前 Capture atomicity。

2.2 Non-fatal：直接 skip relation，Capture 继续

以下不要再当成整个 Capture 的 fatal error：

relation_policy_mismatch
relation confidence < auto-save threshold
endpoint memory_type 不允许该 relation_type
relation direction 不符合 Profile policy
Assistant 临时分析产生的低可信 relation
relation 不满足自动保存规则

行为：

relation proposal
→ validation / policy check
→ skipped
→ Candidate / Memory 正常继续持久化

例如：

relation_skipped_count += 1
reason_code=relation_policy_mismatch

不要：

retry RelationExtractor

也不要：

memory.capture.incomplete

3. Relation 自动保存阈值保持不变

不要为了提高 Relation 成功率降低：

confidence >= 0.90

例如：

confidence=0.6
confidence=0.7

正确行为就是：

skip

而不是：

accept

也不是：

Capture fail

目标语义：

高置信度 + 合法 relation
→ accepted

低置信度 / policy mismatch
→ skipped

结构损坏 / 非法 endpoint / 伪造 provenance
→ retry / fatal

4. 修正 Relation 错误日志

当前存在：

实际 rejected reason：

relation_policy_mismatch

但最终 exception message 却显示：

relation source_expression must occur in the redacted source turn

请去掉这类 hard-coded / 过时错误信息。

最终错误信息应与真实 reason 一致。

例如：

memory.capture.relation_validation_rejected
reason_code=relation_policy_mismatch

或：

InvalidModelOutputError:
relation validation failed:
reason=invalid_source_expression

要求：

日志 reason_code
exception message
retry decision

三者必须一致。

5. Relation retry 只用于真正 retryable 的错误

保留当前：

memory.capture.relation_extraction_attempt.started
memory.capture.relation_extraction_attempt.failed
memory.capture.relation_extraction_attempt.completed

但增加：

retryable=true/false

示例：

retryable

invalid JSON
None output
schema validation failure
unknown endpoint
invalid source_expression

non-retryable

relation_policy_mismatch
confidence below threshold
unsupported direction
profile relation policy rejected

对于 non-retryable：

attempt=1
→ skip

不要浪费三次 LLM 调用。

6. P1：Candidate 原子化只做 Prompt 收紧，不改数据模型

当前 Candidate 已比之前明显原子化，但仍有：

source_expression

只支撑 2025A 数据，而 content 同时包含 2025A + 2026Q1。

本轮不要新增 multi-evidence Candidate。

只在 Extraction Prompt 增加一条强约束：

If candidate content contains facts from multiple periods,
rows, bullets, or sources, and one source_expression cannot
fully support all of them, split them into separate candidates
or keep only the facts supported by that source_expression.

中文规则：

一个 Candidate 的关键事实和数字必须全部被同一个
source_expression 直接支撑。

如果跨时期、跨表格行、跨 bullet，
优先拆成多个 Candidate。

不要为 Evidence Coverage 再新增复杂 validator。

7. P1：Assistant / memory-system 元信息保持当前策略即可

当前 Assistant 复述或系统状态说明偶尔仍会被 Extractor 抽取，但后续通常会因：

invalid_source_expression

被丢弃，没有真正污染 Active Memory。

本轮不再新增：

semantic restatement detector
memory meta classifier
额外 LLM filter

如果要做，只允许 Prompt 级小修改：

Do not extract:
- memory system state
- review/timeline status
- assistant restatement of recalled memory
- assistant explanation of what memories are missing

不要新增架构组件。

验收 Case

Case 1：明确长期研究判断

输入用户明确长期 thesis / risk / ongoing_research / preference。

预期：

capture status=completed
auto_saved_count > 0

Relation 若合法且 confidence >= 0.9：

relation_accepted_count > 0

Case 2：不确定猜测 → Pending

输入：

我现在还有几个没有证据支持的猜测：

1. 公司下半年可能有新的海外客户；
2. 两个大型汽车项目也许都会提前完成验收；
3. NRR 可能进一步提升到 115% 以上。

这些都只是猜测，目前没有足够证据。

预期：

capture status=completed
pending_count > 0

即使 RelationExtractor 提出：

supports
challenges
threatens

只要：

confidence < 0.9

或：

relation_policy_mismatch

应：

relation_skipped_count > 0

但：

memory.capture.incomplete

不能出现。

这是本轮最重要的验收。

Case 3：Relation fatal error

Mock：

source_memory_id = 不存在

或：

source_expression = source turn 中完全不存在

预期：

relation attempt 1 failed
→ retry

全部 attempt 失败后：

capture status=failed
failure_code=invalid_candidate_output

确保真正的不可信模型输出仍然保持 fail-closed。

Case 4：Relation policy mismatch

Mock：

relation_type 合法
endpoint 合法
但不符合 Profile relation policy

预期：

retryable=false
relation_skipped_count=1
capture status=completed

Case 5：低置信度 Relation

Mock：

confidence=0.7

预期：

relation_accepted_count=0
relation_skipped_count=1
capture status=completed

不要 retry。

Case 6：核心跨会话 Recall

Session A：

用户明确长期判断
→ auto-save

Session B：

我要继续跟踪恒川工业软件，
请基于之前的研究判断分析下一季度。

预期：

memory.recall.result_count > 0
rendered_context != ""

确认最后一轮 Relation 修改没有破坏核心 Recall。

不再继续优化的内容

本轮完成后，以下内容直接列为 Known Limitations：

Assistant 回声仍可能造成少量无效 Extraction；

某些 external fact Candidate 仍可能跨时期；

Recall repository 性能仍有优化空间；

Timeline / Relation 复杂场景不保证全部自动成功；

不做 multi-evidence Candidate；

不做复杂 salience pruning；

不做额外 LLM query rewrite；

不追求所有 relation 自动建边。

最终停止条件

只要满足：

1. User explicit → Auto-save 稳定
2. Uncertain → Pending 稳定
3. 新会话 Recall 稳定
4. 合法高置信度 Relation 可以成功写入
5. 普通 Relation skip 不再拖垮 Capture
6. 真正非法 Relation 仍然 fail-closed

即可停止继续修改代码。

不要再因为边缘 Case 增加新的子系统。