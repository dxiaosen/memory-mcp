Memory MCP 最终收敛与边界修复方案

目标：先修复当前剩余的语义与生命周期问题，再做一次“只减不增”的项目收敛，最后从代码层隔离 Hook Runtime API 与模型可见的 Memory Management API，并用一个干净的 Claude Agent 做最终 E2E 验收。

本轮原则：不再扩展功能，不再增加平台型抽象，不再重写已稳定的 Capture/Recall 主链。

1. 当前状态判断

从最新一轮日志看，Memory MCP 的基础链路已经基本稳定：

Candidate structured output 已明显稳定，绝大多数 extraction attempt=1 成功；

文档来源候选可以进入 Pending；

用户明确长期基准可以 Auto-save；

新会话 BeforeRun Recall 能命中已保存记忆；

Relation policy mismatch 已经可以 skip，而不是回滚整个 Capture；

单 Candidate 的非法 source_expression 可以 discard，而不是拖垮整个 Batch；

Hook 的 capture_completed_turn 和 recall_memory 主流程工作正常。

当前真正需要解决的只剩四类问题：

明确不确定/猜测仍可能被 Auto-save

明确修正旧 thesis 时没有正确 replacement / supersede

模型会把普通业务语义误判为 Memory MCP 管理命令

manual relation 与 automatic relation 可能重复写入

另外需要补一个单独回归：

revoke memory 时 active relation 是否能正确 stale/cascade

2. Phase A：修复剩余 P0 语义问题

A1. 明确不确定性优先于 explicit durable

问题

当前 Admission 逻辑对：

user + explicit + durable + confidence >= threshold

倾向直接：

auto_save

但以下表达虽然是“用户明确表达”，表达的内容本身却是“不确定”：

我猜 Q3 NRR 可能回到 111%
这只是猜测
目前没有足够证据
暂时不能确认
不要把它当成已确认长期判断

这种内容不能因为 expression_basis=explicit 就进入 Active。

目标语义

优先级调整为：

explicit uncertainty
    >
explicit durable statement

即：

用户明确表达不确定 / 猜测 / 未验证
→ Pending

否则：
用户明确表达 durable judgment/preference/fact
→ 正常进入现有 Admission

推荐实现

不要新增复杂 EpistemicPolicy 或新的领域层。

在现有 Admission / candidate processing 中增加一个小的确定性函数：

def has_explicit_uncertainty(candidate, source_text: str) -> bool:
    ...

只判断与 Candidate 的 source_expression 邻近的用户原文。

建议覆盖中文：

猜测
可能
也许
或许
暂时
不确定
未经验证
没有足够证据
缺乏证据
不能确认
尚不能确认
不要当成已确认
不要作为已确认
只是一个假设
只是猜测

英文可保留少量：

maybe
might
possibly
uncertain
unverified
not enough evidence
do not treat as confirmed
hypothesis

Admission 顺序

推荐：

if blocked:
    BLOCKED
elif invalid_candidate:
    DISCARD
elif explicit_uncertainty:
    PENDING(reason="explicit_uncertainty")
elif non_user_source:
    PENDING(...)
elif system_inference:
    PENDING(...)
elif low_confidence:
    PENDING(...)
elif explicit_durable_statement:
    AUTO_SAVE
...

重点：

explicit_durable_statement

不能覆盖：

explicit_uncertainty

不要做

不要：

给 Candidate 增加 epistemic_state 新字段；

新增“假设图谱”；

修改 assertion_kind 枚举；

给所有 research_question 强制 Pending。

research_question 本身可以是用户明确长期跟踪的研究问题；只有明确不确定的结论/假设需要这条规则。

测试

至少覆盖：

“我猜 Q3 NRR 可能回到 111%，但目前没有足够证据。”
→ Pending(explicit_uncertainty)

“未来两个季度重点验证 NRR 能否回到 111%。”
→ 可以作为 ongoing_research / research_question 正常准入

“我确认 Q3 NRR 已经回到 111%。”
→ 不命中 uncertainty

A2. 修复明确 thesis 修正没有 replacement 的问题

问题

当前 Lifecycle 已有 explicit replacement 机制，但实际测试中：

我现在明确修正之前的长期判断
我不再认为 X
以后更核心的判断应该是 Y
以后请以更新后的判断为准

仍出现：

replacement_count=0

并产生新的独立 thesis / research_decision，而旧 thesis 继续 Active。

最终 Recall 就会同时看到旧、新判断，甚至旧 thesis 得分更高。

根因重点检查

当前设计是：

同 subject + type
→ find_current
→ duplicate / explicit replacement / ambiguous conflict

问题很可能不是 _EXPLICIT_REPLACEMENT 正则本身，而是：

新 Candidate 的 subject
!=
旧 thesis subject

导致 lifecycle 根本没有找到旧目标。

也可能是 CandidateExtractor 把一个“完整修正”拆成多个 Candidate：

research_decision
thesis
ongoing_research

从而没有稳定形成一个可以替换旧 thesis 的目标。

修复原则

不要引入 version_group、topic graph 或新的 lifecycle engine。

只增强现有 replacement target resolution。

推荐策略

对于满足明确替换语义的 user Candidate：

source_role=user
expression_basis=explicit
memory_type=thesis / research_preference / ongoing_research / decision-like type

在 same subject + same type 精确匹配失败后，允许一次有界 replacement fallback：

same owner
same profile
same memory_type
active/current/effective
top K <= 5

然后使用现有 embedding / lexical 相似度找最相关旧 Memory。

仅在：

用户原文明确 replacement
+
候选与旧 memory 高相关
+
只有一个明显目标

时执行 replacement。

否则：

ambiguous_lifecycle_target
→ Pending

推荐阈值

不要新做复杂 ranking。

可以复用现有 recall/tokenizer/embedding 能力，或者一个简单组合：

subject lexical overlap
content embedding similarity

要求明显唯一候选，例如：

top1 >= threshold
and top1 - top2 >= margin

阈值沿用现有系统习惯，不要为了通过测试无限放宽。

更重要：CandidateExtractor 的行为

Prompt 只补一条，不要继续膨胀：

When the user explicitly replaces or corrects an earlier thesis,
prefer one complete thesis candidate representing the new current judgment.
Do not split the replacement itself into multiple overlapping thesis/decision candidates.

Replacement 成功后

必须保持现有语义：

same MemoryItem
old revision:
    lifecycle_status=superseded
    is_current=false

new revision:
    lifecycle_status=active
    is_current=true

relations bound to old revision:
    stale(endpoint_revision_changed)

最终：

replacement_count=1

测试

Round 1:
“如果订阅占比提升且 NRR >110%，收入质量改善”
→ thesis active

Round 2:
“我现在修正之前的判断。我不再认为订阅占比提升本身足够。
以后只有 NRR≈110%+、OCF 不明显落后利润、AR days 不持续恶化时，
才认为收入质量改善。以后以这个判断为准。”
→ same memory_id
→ revision +1
→ old superseded
→ replacement_count=1

Fresh Recall:
→ 只返回 current/new thesis
→ 旧 thesis 不参与 active recall

A3. Relation 继续保持 best-effort

当前这一点已经基本修好，不要重新收紧。

目标保持：

Candidate Capture = 主链
Relation = enhancement

Relation 中：

policy mismatch
low confidence
invalid source expression
malformed proposal
endpoint unavailable

都只能：

skip / warning

不能回滚已合法的 Candidate。

仅 Repository 主事务本身不可提交时才允许 Capture 失败。

不要再次把 Relation validation 改回 Capture-fatal。

A4. 单 Candidate 错误继续 Candidate-level discard

当前已经可以做到：

invalid_source_expression
→ candidate discard
→ capture continues

继续保持。

同理下面这些也应该 Candidate-level：

invalid_memory_type
invalid_progress
profile mismatch
unsupported candidate field semantics

只有整个 structured batch 无法解析时才：

invalid_candidate_output

3. Phase B：修复模型主动调用 MCP 的边界

这是本轮最重要的架构收敛点。

B1. 明确两类 API

Memory MCP 实际上有两个调用面。

Runtime API

只给 Host Hook：

recall_memory
capture_completed_turn

其中：

capture_completed_turn

必须是 Hook-only。

Memory Management API

给模型在用户明确管理记忆时使用：

list_memories
get_memory
search_memories
list_pending_reviews
get_memory_stats

confirm_pending_memory
batch_confirm_pending
reject_pending_memory
revoke_memory
link_memories
revoke_memory_relation

B2. 普通业务语义不等于 Memory Management

以下都属于普通业务内容：

“我现在修改之前的投资判断……”
“以后以这个判断为准。”
“Q2 NRR 的下降挑战了我之前的 thesis。”
“这个风险威胁我的核心判断。”
“我不再看好海外客户这个逻辑。”

正确路径：

User
→ Claude 正常业务回答
→ AfterRun Hook
→ Candidate / Lifecycle / Relation

不能变成：

Claude
→ search
→ confirm
→ revoke
→ link

只有以下明确治理语义才能主动 mutation

“查看记忆库里有哪些记录”
“确认这个 Pending”
“拒绝这条 Pending”
“把这条已存储的记忆撤销掉”
“撤销 memory_id=xxx”
“手工把 memory A 和 memory B 建成 challenges”

4. Phase C：从代码层限制模型调用

仅靠 CLAUDE.md 不够。

目标是：

Prompt soft guard
+
tools/list visibility guard
+
CallTool authorization hard guard

三层共同生效。

C1. 最小改造：复用现有 read/write/review scopes

当前已有：

memory:read
memory:write
memory:review

不要再新增一套 RBAC 框架。

建议重新明确其职责：

memory:read
    = read-only memory inspection

memory:write
    = runtime ingestion only

memory:review
    = explicit user memory governance / management

工具映射调整

capture_completed_turn      → memory:write

recall_memory               → memory:read

list_memories               → memory:read
get_memory                  → memory:read
search_memories             → memory:read
list_pending_reviews        → memory:read
get_memory_stats            → memory:read

confirm_pending_memory      → memory:review
batch_confirm_pending       → memory:review
reject_pending_memory       → memory:review
revoke_memory               → memory:review
link_memories               → memory:review
revoke_memory_relation      → memory:review

最关键的改动：

link_memories

不要再使用 memory:write。

这样 memory:write 就可以真正变成 Runtime-only。

C2. 使用两个 Token

Hook Token

{
  "scopes": [
    "memory:read",
    "memory:write"
  ]
}

用于：

BeforeRun recall
AfterRun capture

Interactive Agent Token

{
  "scopes": [
    "memory:read",
    "memory:review"
  ]
}

用于 Claude MCP。

因此 Claude 即使知道：

capture_completed_turn

的名字，也没有 memory:write，调用必须返回：

permission_denied

C3. ListTools 必须按 Principal 过滤

不要只在 CallTool 时拒绝。

理想状态是 Claude 的 tools/list 根本看不到：

capture_completed_turn

定义一个简单映射即可，不要新建 PolicyEngine：

TOOL_SCOPES = {
    "capture_completed_turn": {"memory:write"},

    "recall_memory": {"memory:read"},
    "list_memories": {"memory:read"},
    "get_memory": {"memory:read"},
    "search_memories": {"memory:read"},
    "list_pending_reviews": {"memory:read"},
    "get_memory_stats": {"memory:read"},

    "confirm_pending_memory": {"memory:review"},
    "batch_confirm_pending": {"memory:review"},
    "reject_pending_memory": {"memory:review"},
    "revoke_memory": {"memory:review"},
    "link_memories": {"memory:review"},
    "revoke_memory_relation": {"memory:review"},
}

ListTools

def visible_tools(principal):
    return [
        tool
        for tool in ALL_TOOLS
        if TOOL_SCOPES[tool.name] & principal.scopes
    ]

CallTool

必须再次校验：

def authorize_tool_call(principal, tool_name):
    required = TOOL_SCOPES[tool_name]

    if not (required & principal.scopes):
        raise PermissionDenied(...)

不能因为 ListTools 已过滤就省略 CallTool 校验。

C4. 如果 MCP SDK 不方便动态过滤 ListTools

不要为了动态 ListTools 重写 MCP Server。

直接使用两个 façade。

/mcp/runtime
    recall_memory
    capture_completed_turn

/mcp/agent
    recall/list/get/search/list_pending/stats
    confirm/reject/revoke/link/...

两个 façade：

共用同一个：
Application services
Repository
PostgreSQL pool
Profile registry
Embedding
Extractor

不是两套 Memory MCP。

只是：

two protocol façades
→ one application core

推荐优先级

如果当前 SDK 很容易按 Principal filter ListTools
→ 单 endpoint + scope filtering

如果实现起来需要明显侵入 MCP SDK
→ 两 façade endpoint

不要自己造 MCP 中间件框架。

5. Phase D：限制“隐式治理”

即使模型拥有 memory:review，Server 也不能帮它完成未授权的组合操作。

D1. Pending endpoint 不允许 link 自动确认

当前不应出现：

link needs active endpoint
→ model calls confirm_pending_memory
→ link

除非用户明确说：

“确认这条 Pending，然后建立关系”

Server 侧：

link_memories(endpoint=pending)
→ relation_endpoint_not_active

即可。

不要：

auto_confirm=True

不要让 link 内部调用 confirm。

D2. Relation semantic dedupe

当前设计已经声称相同：

owner/source/target/type

应该幂等，但 manual/item 与 automatic/revision 仍可能形成两条语义等价边。

本轮修复要区分：

revision lifecycle identity

automatic/revision

需要 source_revision_id / target_revision_id。

semantic active relation identity

对当前 Active 图，应该至少避免：

same owner
same profile
same source_memory_id
same target_memory_id
same relation_type
status=active

同时存在两条。

推荐实现

不要删除 origin/scope。

增加 Repository 级 semantic lookup：

find_active_relation(
    owner_id,
    profile_id,
    source_memory_id,
    target_memory_id,
    relation_type,
)

写入前：

已有 active semantic relation
→ 返回 existing relation
→ duplicate=true / replayed-like result
→ 不插入新 row

如果数据库允许，可增加部分唯一索引：

UNIQUE (
    owner_id,
    profile_id,
    source_memory_id,
    target_memory_id,
    relation_type
)
WHERE status = 'active';

前提是确认 replacement stale 流程能先把旧 relation 转成 stale，再创建针对新 revision 的 relation。

如果现有 schema 因 revision 语义无法直接加这个唯一索引，就在 Repository 事务内做：

SELECT ... FOR UPDATE
→ semantic dedupe
→ insert

不要为了这个引入 RelationRegistry / GraphStore 新层。

6. Phase E：补 revoke cascade 回归

现有设计要求：

memory revoked / superseded / expired
→ relation 不再 active

补单独测试：

Memory A(active)
Memory B(active)
A --challenges--> B(active)

revoke_memory(A)

Expected:
A current revision → revoked
relation → stale
stale_reason=endpoint_revoked

get_memory(B)
→ 默认不返回该 stale relation

include_history=true
→ 可以看到 stale relation

如果当前 revoke_memory 会碰数据库 CHECK constraint：

修 Repository 事务顺序：

1. lock endpoint
2. stale active relations referencing endpoint
3. revoke current revision
4. commit

不要要求调用者：

先 revoke relation
再 revoke memory

7. Phase F：项目收敛 / 去冗余

这一阶段的目标不是“重构得更优雅”，而是：

删除不再需要的代码
合并重复实现
冻结高级功能
降低后续维护成本

F1. 明确保留的核心路径

以下不能为了收敛而删除：

Capture
Recall
PostgreSQL Repository
Embedding
Candidate validation
Admission
Pending Review
Evidence / provenance
Duplicate
Replacement / Revision lifecycle
Relation（自动 + 显式管理）
Auth / owner isolation
BeforeRun / AfterRun Agent
InvestmentResearchProfile
GeneralWorkProfile

F2. Advanced features：冻结，不继续扩

如果已有测试且没有明显负担，可以保留：

Team Memory
Maintenance
expiry materialization
expiry-derived reminder
timeline-like metadata

但本轮：

不新增功能
不继续优化策略
不为它们新增抽象

如果其中存在完全未引用、无测试、无真实入口的旧实验代码，再删除。

不要因为“不是当前 V2 主线”就贸然删除已经稳定工作的功能。

F3. 重点检查以下冗余

让模型逐项 grep / AST / tests 检查，不要凭名字猜。

1. 重复 normalization

检查：

source_expression normalization
CJK whitespace normalization
query normalization
subject normalization
relation source normalization

如果完全重复：

→ 保留一个纯函数

不要做 NormalizationService。

2. structured output adapter 重复逻辑

检查：

CandidateExtractor parse
RelationExtractor parse
DeepSeek JSON parse
retry parse
schema unwrap

如果存在重复 provider parsing：

→ 一个小 helper

但 Candidate 与 Relation schema validation 保持独立。

不要做 BaseExtractor hierarchy。

3. retry 重复

检查：

candidate extraction retry
relation extraction retry
agent transport retry

注意它们语义不同。

只合并“完全相同的模型 structured retry”。

Agent transport retry 继续独立。

不要做 GenericRetryManager。

4. relation outcome 分类重复

如果 accepted / skipped / fatal 在多处重复判断：

→ 一个小函数 / enum mapping

不要做 RelationPolicyEngine。

5. tool auth mapping 分散

把：

tool → scope

收敛为一个权威映射。

ListTools 与 CallTool 共同引用。

不要在每个 tool decorator 里散落 scope string。

6. DTO / model 重复

检查：

MemoryView
MemorySummary
MemoryRecord
MemoryHistoryEntry
RelationView
internal write models

只有字段与语义完全相同才合并。

不要为了 DRY 把 domain model 直接暴露成 MCP DTO。

7. compatibility / dead branches

搜索：

deprecated
legacy
compat
fallback
old
v0
TODO remove
temporary

逐项确认是否还有测试/调用方。

无调用、无测试、无外部契约：→ 删除。

有 migration/history 兼容意义：→ 保留并写注释。

8. 配置项

检查 Settings：

是否有永远未读取的 env
是否有两个配置控制同一件事
是否有早期实验残留 model setting
是否存在 v4-pro / v4-flash 多个来源不一致

模型配置必须最终做到：

日志中的 model_id
=
实际配置来源
=
文档说明

F4. 明确禁止的“收敛方式”

不要新增：

BaseExtractor
GenericValidationPipeline
PolicyEngine
RetryManager
PromptBuilder hierarchy
RepositoryFactory hierarchy
EventBus
Middleware framework
Service locator
Plugin system
Graph abstraction

当前项目已经够复杂。

优先：

删除
内联
小函数
小映射
减少分支

8. Phase G：文档同步

修改完代码后，只同步真正的当前设计。

重点更新：

docs/design.md
docs/agents.md
docs/testing.md
docs/config.md

需要明确：

1. automatic recall/capture 是 Hook runtime 行为
2. capture_completed_turn 不暴露给 interactive model
3. memory:write 是 runtime ingestion
4. memory:review 是 explicit memory management
5. business semantic update 不等于 MCP mutation
6. Relation 是 best-effort enhancement
7. explicit uncertainty → Pending
8. explicit replacement → supersede

删除已经失效的旧描述。

OpenSpec 保留变更历史，不要把历史 change 当作当前设计重复写进 docs。

9. 实施顺序

严格按以下顺序：

Step 1
explicit uncertainty → Pending

Step 2
replacement target fallback + thesis revision test

Step 3
tool scope remap
link_memories: write → review

Step 4
interactive/runtime token split

Step 5
ListTools filtering + CallTool hard authorization
或两 façade endpoint

Step 6
relation semantic dedupe

Step 7
revoke cascade test/fix

Step 8
项目去冗余

Step 9
文档同步

Step 10
最终 E2E

不要把“收敛重构”和 P0 行为修复混在同一个大提交里。

建议至少拆成：

fix: uncertainty and replacement semantics
fix: isolate runtime and management MCP tools
fix: dedupe active memory relations
refactor: remove redundant memory-mcp code paths
docs: align final memory architecture

10. 最终测试矩阵

Test 1：空库

BeforeRun
→ result_count=0
→ rendered_context=""

Test 2：长期基准

用户：

以下是我当前长期研究基准……
以后以此为准。

期望：

thesis → Auto-save
risk → Auto-save
ongoing_research → Auto-save
durable preference → Auto-save

Test 3：Fresh Recall

新 Claude session，不读 Claude 内置 memory。

期望：

BeforeRun result_count > 0
Agent recalled_count > 0
回答使用 Memory MCP context

Test 4：明确猜测

我猜 Q3 NRR 可能回到 111%。
这只是猜测，目前没有足够证据，
请不要把它当作已确认长期判断。

期望：

auto_saved_count=0
Pending(explicit_uncertainty)

Test 5：明确修正 thesis

我现在明确修正之前的长期判断。
我不再认为订阅收入占比提升本身足够说明收入质量改善。
以后只有 NRR≈110%+、OCF 不明显落后利润、
应收账款周转天数不持续恶化时，
我才认为收入质量改善。
以后以这个判断为准。

期望：

replacement_count=1
same memory_id
new revision active/current
old revision superseded/non-current

模型不得主动：

revoke_memory
confirm_pending_memory
link_memories

Test 6：Fresh Recall latest thesis

新 session：

继续分析恒川。
基于我最新的长期判断，
告诉我下一季度最重要的验证指标。

期望：

只使用 current/new thesis
旧 thesis 不出现在 active Recall

Test 7：自然语言 Relation

用户：

2026Q2 NRR 从 112% 降到 109%，
这挑战了我之前关于 NRR 稳定性的判断。

期望：

Claude 正常回答
Claude 不主动 link_memories
AfterRun automatic relation:
evidence_claim --challenges--> thesis

Test 8：显式 Memory Management

用户：

查看当前有哪些 Pending 记忆。

允许 Claude：

list_pending_reviews

然后：

确认 review_id=xxx

允许：

confirm_pending_memory

如果用户没有明确确认：

禁止 confirm

Test 9：显式手工 Relation

用户：

请把 memory A 和 memory B 手工建立为 challenges 关系。

允许：

link_memories

若 automatic relation 已存在：

返回 existing
不创建第二条 active semantic relation

Test 10：revoke cascade

用户：

请撤销 memory A。

期望：

Memory A → revoked
关联 active relations → stale(endpoint_revoked)
无 DB constraint error

11. Stop Condition

满足以下条件后停止 Core 功能开发：

1. explicit user durable statement 稳定 Auto-save
2. explicit uncertainty 稳定 Pending
3. fresh-session Recall 稳定命中
4. explicit thesis correction 稳定 replacement
5. old revision 不参与 active Recall
6. Relation failure 不影响 Candidate Capture
7. automatic/manual semantic relation 不重复
8. model 无法调用 capture_completed_turn
9. 普通业务语义不会触发 memory mutation
10. explicit memory governance 可以正常调用管理工具
11. revoke endpoint 不破坏 relation 约束

剩余以下问题全部列为 Known Limitations，不继续扩：

文档 Candidate 偶尔不够原子
source_expression coverage 不完美
Assistant inference Pending 偏多
Capture latency 10~30s
复杂多跳 Relation
Team Memory 策略精细化
复杂 Timeline

到这里项目应进入：

freeze core
→ clean docs
→ final demo
→ write evaluation/report

12. 给编码模型的最终执行要求

请直接基于现有代码实施本方案。

要求：

- 先读现有 design / tests / implementation，再修改。
- 保持 MCP DTO、数据库核心 schema、owner 隔离和现有外部行为。
- 不引入新的通用框架或大层级抽象。
- 优先复用现有 Principal/scopes、Lifecycle、Repository 和 structured model adapter。
- 每个修复先补/改测试，再改代码。
- 对已有稳定功能只做必要修改。
- 最后做一次 dead code / duplicate helper / unused setting 检查。
- 删除冗余必须有 grep/test 证据，不凭主观判断删除。
- 所有测试通过后同步当前设计文档。