1. 本轮目标

本轮不再扩展 Memory MCP 能力，只修复当前 E2E 中已经暴露出的 4 个边界问题：

RelationExtractor 失败不应回滚合法 Candidate；

单个 Candidate 的业务字段错误不应让整个 Capture 失败；

Claude 主动调用 mutation MCP 的边界需要进一步收紧；

revoke_memory 撤销带有 Active Relation 的 Memory 时，应由服务端完成关系级联失效，而不是抛数据库约束错误。

本轮完成后，停止继续扩展 Core。

2. 当前已确认稳定的部分

以下能力本轮不再修改：

Candidate structured output 基本稳定；

文档事实可以进入 Pending；

用户明确长期判断可以 Auto-save；

Fresh-session Recall 可以命中 Active Memory；

不确定猜测可以进入 Pending，而不是 Auto-save；

自动 threatens Relation 可以成功建立；

自动 challenges Relation 可以成功建立；

普通 relation_policy_mismatch 已能 skip，而不是直接失败；

Hook Capture / Recall 主链正常；

PostgreSQL、Embedding、Recall threshold、Admission threshold 暂不调整。

因此本轮不要继续修改：

Candidate Prompt 大框架；

Relation Prompt 大框架；

Relation threshold；

Recall threshold；

Admission threshold；

Memory types；

Relation policy 类型设计；

Timeline；

Team Memory；

Evidence 多源架构；

PostgreSQL Schema（除非修复 relation terminal-state 约束必须做极小调整）。

3. P0：Relation 必须彻底改成 Best-Effort Enhancement

3.1 当前问题

当前 Capture 主流程中，Candidate 已经合法抽取、验证、Admission，但 RelationExtractor 如果连续产生非法结果，例如：

invalid_source_expression；

unknown / stale endpoint；

malformed relation proposal；

relation validator structural rejection；

最终仍可能导致整个 Capture：

status=failed
failure_code=invalid_candidate_output

这会造成：

合法 Candidate
→ 已完成 extraction / validation / admission
→ RelationExtractor 出错
→ 整个 Capture rollback
→ 合法 Memory / Pending 丢失

对于当前项目定位，这个失败语义过重。

3.2 新的责任边界

改成：

Candidate Capture = 主链
Relation Extraction = best-effort 增强链

目标流程：

CompletedTurn
    ↓
Candidate Extraction
    ↓
Candidate Validation
    ↓
Admission / Lifecycle
    ↓
Memory / Pending / Replacement 已确定
    ↓
Relation Extraction
    ├─ success → 保存 relation
    └─ failure → warning + skip relation
    ↓
Capture completed

3.3 Relation 失败语义

以下 Relation 错误全部只影响 Relation，不允许回滚 Candidate：

invalid_source_expression
unknown endpoint
stale endpoint
endpoint type mismatch
relation type mismatch
invalid direction
below threshold
policy mismatch
malformed relation proposal
model structured-output failure
relation extraction retry exhausted

最终记录：

relation_status=failed/skipped
relation_error_code=<reason>
relation_accepted_count=0
Capture status=completed

3.4 Retry

RelationExtractor 可以继续保留最多 3 次 bounded retry，但 retry exhausted 后：

不要 raise 到 Capture 顶层
不要 rollback Candidate

建议日志：

memory.capture.relation_extraction_failed
capture_id
attempts
error_code
error_message
candidate_persistence_preserved=true

3.5 原子性重新定义

Capture 的原子性只保证：

Candidate / Admission / Lifecycle / Memory persistence 自身一致。

Relation 不再参与主 Capture 的原子事务边界。

Relation 是派生增强数据，可以失败、重试或后续补算。

4. P0：单 Candidate 错误改为 Candidate-Level Reject

4.1 当前问题

模型偶尔可能产生：

memory_type = external_fact

但 external_fact 实际应属于：

assertion_kind

而 investment-research profile 的 memory_type 应为：

research_preference
research_question
thesis
evidence_claim
risk
catalyst
ongoing_research
research_decision

这种错误目前可能导致整个 CandidateBatch / Capture 失败。

4.2 新规则

只要 CandidateBatch 顶层 schema 可以解析：

单条 Candidate 非法
→ discard candidate
→ 其他 Candidate 继续

Candidate-level reject 包括：

invalid_memory_type
invalid_assertion_kind
invalid_source_expression
invalid_profile_value
invalid_business_progress
invalid_candidate_field
unsupported_candidate_semantics

Admission 输出：

decision=discard
reason_code=invalid_memory_type

4.3 只有这些情况才允许整个 Candidate Extraction fail

provider 返回 None 且 retry exhausted
CandidateBatch 顶层完全无法解析
JSON/schema 完全损坏
返回值不是 CandidateBatch 语义对象

即：

Batch structural failure → retry / final fail
Candidate business validation failure → discard only

5. P0：修复 revoke_memory 与 Active Relation 的级联失效

5.1 当前问题

当某个 Active Memory 上存在 Active Relation 时，直接：

revoke_memory(memory_id)

可能触发 PostgreSQL：

CheckViolation
memory_relations_terminal_state

调用者必须手动：

revoke_memory_relation
→ revoke_memory

这暴露了 Service / Repository 边界问题。

调用者不应该理解 relation terminal-state 的底层约束。

5.2 目标行为

revoke_memory 应在同一业务事务内：

revoke_memory(memory A)
    ↓
查询所有涉及 A 的 active relations
    ↓
将这些 relation 标记为 stale / revoked
    ↓
stale_reason = endpoint_revoked
stale_at = now
    ↓
revoke memory A
    ↓
commit

建议优先使用：

relation.status = stale
stale_reason = endpoint_revoked

因为 Relation 本身不一定是“被用户显式撤销”，只是 endpoint 已失效。

5.3 事务要求

必须做到：

relation stale
+
memory revoke

同一事务成功或失败。

不能出现：

Memory revoked
Relation 仍 active

也不能把数据库 CheckViolation 直接暴露给 MCP Client。

5.4 单元测试

至少覆盖：

1. revoke 无 relation 的 memory
   → success

2. revoke source endpoint
   → relation stale
   → memory revoked

3. revoke target endpoint
   → relation stale
   → memory revoked

4. 一个 memory 多条 active relations
   → 全部 stale
   → memory revoked

5. relation stale 过程中失败
   → 整个事务 rollback

6. 重复 revoke
   → idempotent / 明确 terminal result

6. P0：重新定义 Claude 主动调用 MCP Mutation 的边界

6.1 当前问题

Claude 会把普通业务语义：

“我修正之前的判断”
“以后以新判断为准”
“Q2 NRR 下滑挑战了之前的判断”

错误理解成：

revoke_memory
link_memories
confirm_pending_memory

随后模型开始探测 Memory MCP 数据结构和 Relation policy，造成测试状态污染。

6.2 正确边界

普通业务语义

下面这些都属于普通对话：

我改变观点了
我不再认可原判断
以后以这个新判断为准
这个事实挑战了之前的 thesis
这个风险威胁原判断
这个数据支持之前的逻辑

正确处理：

Claude 正常回答
↓
AfterRun Hook
↓
Candidate / Lifecycle / RelationExtractor 自动处理

Claude 不允许主动调用 mutation MCP。

显式 Memory 管理语义

只有用户明确表达“管理已存储 Memory MCP 数据”的意图时，才允许主动调用 mutation tools，例如：

看看 Memory MCP 里存了什么
把 memory_id=xxx 的记忆撤销
把这个 Pending 确认掉
拒绝这条 Pending
手动把 memory A 和 memory B 建成 challenges
删除这条存储的关系

此时才可以调用：

confirm_pending_memory
batch_confirm_pending
reject_pending_memory
revoke_memory
link_memories
revoke_memory_relation

7. 更新 CLAUDE.md

建议项目根目录规则调整为：

# Memory MCP Test Rules

This project uses Memory MCP as its external long-term memory system.

- Do not use Claude's built-in MEMORY.md or project memory as long-term memory for this test.
- Automatic memory recall and capture are handled by the configured BeforeRun and AfterRun hooks.
- Never call `capture_completed_turn` directly.
- Do not manually persist ordinary conversation content.

## Business updates are not memory-management commands

- A user changing, correcting, replacing, or updating a business judgment is normal conversation content, not a direct Memory MCP management command.
- A user saying that one research fact supports, challenges, threatens, or resolves another judgment is normal semantic content.
- Let the AfterRun hook handle candidate extraction, replacement, lifecycle updates, and automatic relation extraction.
- Do not call `revoke_memory`, `confirm_pending_memory`, `link_memories`, `revoke_memory_relation`, or other mutation tools merely because the user's business statement implies a memory update.

## When mutation tools are allowed

- Use Memory MCP mutation tools only when the user explicitly asks to inspect or manage stored Memory MCP records themselves.
- Examples include explicitly asking to confirm a Pending record, revoke a stored memory, remove a stored relation, or manually link two known stored memories.

8. MCP Tool Description 同时增加 Server-Side 提示

不要只依赖 CLAUDE.md。

对 mutation tool 的 description 增加统一提示。

例如 revoke_memory：

Use this tool only when the user explicitly asks to revoke or manage a stored Memory MCP record.
Do not call it merely because the user changes or corrects a business judgment; normal semantic updates are handled by the AfterRun capture lifecycle.

link_memories：

Use this tool only for explicit manual management of stored memory relations.
Do not call it merely because the user says one fact supports, challenges, or threatens another judgment; automatic relation extraction handles normal conversation semantics.

confirm_pending_memory：

Use only when the user explicitly asks to confirm a stored pending review.
Do not auto-confirm pending records based on ordinary conversation context.

目标是让普通 Agent 在 tools/list 阶段就理解工具责任边界。

9. 可选：进一步做 Tool Exposure 隔离

如果当前 MCP Server 容易实现，可以进一步分层：

Hook-only:
  capture_completed_turn

Read:
  recall_memory
  list_memories
  get_memory
  search_memories
  list_pending_reviews
  get_memory_stats

Manage:
  confirm_pending_memory
  batch_confirm_pending
  reject_pending_memory
  revoke_memory
  link_memories
  revoke_memory_relation

最好让：

Hook token → runtime scope
Claude token → read + manage scope

capture_completed_turn 不出现在普通 Claude 的 tools/list。

但本轮如果实现成本较高，可以先只改 tool description + CLAUDE.md，不做大规模鉴权重构。

10. Replacement / Supersede 验收要求

这是本轮真正需要重新验证的主链。

Step 1：建立旧 thesis

用户：

如果订阅与维护收入占比持续提升，同时 NRR 稳定在 110% 以上，
那么收入质量和盈利稳定性有望改善。

期望：

thesis auto_save
old_thesis = active

Step 2：明确修正 thesis

用户：

我现在明确修正之前的长期判断。
我不再认为订阅收入占比提升本身就足以说明收入质量改善。

以后更核心的判断是：
只有当 NRR 能持续在 110% 左右或以上、
经营现金流没有明显落后于利润、
且应收账款周转天数没有持续恶化时，
我才会认为公司的收入质量真正改善。

以后以更新后的判断为准。

Claude 不应该主动调用：

revoke_memory
link_memories

只正常回答。

AfterRun 应实现：

new thesis auto_save
old thesis superseded / replaced
new thesis active
Capture completed

即使 RelationExtractor 失败：

new thesis 仍必须入库

11. Relation E2E 验收

用户自然语言：

2026Q2 NRR 从 112% 降到 109%，
这挑战了之前“NRR 稳定在 110% 以上时收入质量改善”的判断。

Claude：

正常回答
不要主动 link_memories

AfterRun：

evidence_claim
    --challenges-->
new thesis

如果 RelationExtractor 失败：

Candidate Capture completed
Relation skipped/failed

不得 rollback。

12. 最终 E2E 只跑 6 步

不再跑复杂的完整压力测试。

1. Baseline
   → explicit thesis / risk / ongoing / preference auto-save

2. Fresh Recall
   → result_count > 0

3. Thesis Revision
   → new thesis active
   → old thesis superseded/replaced
   → no proactive mutation MCP

4. Fresh Recall Latest
   → 最新 thesis 优先召回
   → 旧 thesis 不应作为当前核心判断返回

5. Explicit Semantic Relation
   → Claude 不主动 link
   → AfterRun 自动 challenges / threatens

6. Final Recall
   → 最新 thesis + 当前 risk + ongoing research 正确返回

13. 验收标准

必须通过

Candidate structured output 稳定

explicit user thesis
→ auto_save

fresh session
→ recall > 0

uncertain hypothesis
→ pending
→ auto_saved = 0

thesis revision
→ Capture completed
→ new thesis active

RelationExtractor failure
→ does not rollback Candidate

single invalid Candidate
→ discard only
→ does not fail batch

revoke memory with active relation
→ relation automatically stale
→ no PostgreSQL CheckViolation

normal business update
→ Claude does not proactively call mutation MCP

semantic relation statement
→ Claude does not proactively link
→ AfterRun handles relation

可接受 Known Limitations

本轮完成后以下问题允许保留：

Candidate 数量偶尔偏多
Assistant 解释偶尔进入 Pending
source_expression / Evidence Coverage 仍不完美
Capture 可能需要 10~30 秒
RelationExtractor 偶发失败
复杂 Timeline / Multi-hop 未完全验证
Team Memory 未做完整 E2E

这些不再作为继续修改 Core 的理由。

14. Stop Condition

满足下面条件后停止开发 Core：

1. baseline 能稳定 auto-save
2. fresh recall 能稳定命中
3. uncertain 能稳定 Pending
4. thesis revision 不受 Relation 失败影响
5. latest thesis 能在 fresh recall 中优先返回
6. semantic relation 能成功建立，或失败时不影响 Candidate
7. Claude 不再把普通业务更新当成 mutation MCP 命令
8. revoke_memory 可以安全级联 stale active relations

完成以上 8 条后：

Memory MCP Core 进入“功能冻结 / 只修严重 Bug”阶段。

后续如果继续工作，优先做：

测试整理
README / 架构文档
日志收敛
Demo
性能测量

而不是继续增加 Memory Core 复杂度。

15. 本轮实现原则

优先修边界，不扩功能。
优先降低失败传播，不增加智能逻辑。
优先让 Candidate 主链稳定，Relation 永远不能喧宾夺主。
优先让 Hook 处理语义更新，模型 mutation 只处理显式存储管理。
优先删除错误耦合，而不是新增抽象层。

禁止为本轮新增：

BaseExtractor
GenericValidationPipeline
PolicyEngine
RetryManager
MemoryCommandClassifier
EventBus
复杂 Tool Router
新的 Relation Graph 层
新的 Multi-Evidence 架构

如果一个修复可以通过局部 service / validator / repository 调整完成，就不要增加新框架。