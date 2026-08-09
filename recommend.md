Memory MCP 最终稳定性修复方案 V2

1. 本轮目标

本轮只解决两个问题：

Candidate structured output 稳定性

Memory MCP E2E 测试环境隔离

当前不再扩展 Relation、Recall、Admission、Lifecycle、Timeline、Team Memory 或 Evidence 架构。

本轮原则：

先定位 structured output 链路，再做最小修复；先隔离 Claude 内部 Memory，再重新做核心 E2E。

2. 当前已确认的问题

2.1 Candidate structured output 不稳定

当前服务端已经出现过以下几类异常：

CandidateBatch input = None

以及：

CandidateBatch.candidates
Input should be a valid list
input_value={'candidates': [...]}

后一种现象高度疑似某一层发生了重复包装：

正常结构：

{
  "candidates": []
}

异常结构可能变成：

{
  "candidates": {
    "candidates": []
  }
}

因此当前优先怀疑链路：

DeepSeek response
    ↓
provider / SDK structured output
    ↓
adapter
    ↓
parsed Python object
    ↓
CandidateBatch.model_validate(...)

不要继续优先修改 Candidate Prompt。

2.2 Claude 内部项目 Memory 仍然污染测试

即使项目中加入了 CLAUDE.md，Claude 仍可能读取或写入：

~/.claude/projects/<project>/memory/

因此可能出现：

Memory MCP BeforeRun Recall = 0

但 Claude 仍能正确回答历史判断。

这种情况下不能把回答算作 Memory MCP Recall 成功。

所以后续 E2E 必须进行物理隔离，不能只依赖 CLAUDE.md 的软约束。

3. P0：Structured Output 链路诊断

3.1 不先修 Prompt，先观察真实输入输出

在 Candidate structured-output validation 失败时，增加开发态诊断日志。

建议至少记录：

model_id
attempt
finish_reason

raw_message_content
raw_tool_calls
raw_structured_response

provider_parsed_type
provider_parsed_value

candidate_batch_input_type
candidate_batch_input_value

validation_error

生产环境继续保留现有脱敏策略。

开发环境允许记录完整响应，便于确认问题到底发生在哪一层。

3.2 需要明确区分的五种情况

A. DeepSeek 原始响应为空 / None

B. 原始 JSON 正确，但 SDK parsed 结果错误

C. SDK parsed 正确，但项目 adapter 再包装了一层

D. 模型本身输出了重复 wrapper

E. response_format / structured-output 使用方式与 DeepSeek 兼容性不稳定

只有确认是哪一种之后，才修改对应层。

4. P0：DeepSeek V4 Pro 稳定性实验

当前不要直接换模型。

先单独建立一个极小的 extraction stability test，不经过完整 Memory Core。

4.1 固定测试输入

使用一个固定的、简单的 Candidate extraction fixture，例如：

用户：以后分析恒川工业软件时请使用中文，并按照“事实—判断—风险—验证指标”的结构输出。

期望结构固定为一个 CandidateBatch。

4.2 做两组 A/B

A 组

deepseek-v4-pro
thinking = enabled

B 组

deepseek-v4-pro
thinking = disabled

每组建议连续运行：

30～50 次

4.3 统计指标

至少统计：

valid_candidate_batch_count
empty_or_none_count
schema_malformed_count
double_wrapper_count
json_parse_error_count
other_error_count
average_latency_ms
p95_latency_ms

4.4 判定逻辑

如果两组都稳定出现：

{"candidates":{"candidates":[...]}}

优先判断为 SDK / adapter 包装问题。

如果主要出现：

None / empty content

保留 bounded retry，并检查 DeepSeek JSON Output / provider compatibility。

如果 thinking=disabled 明显更加稳定，则 CandidateExtractor 和 RelationExtractor 可以独立使用：

deepseek-v4-pro
thinking = disabled

其他真正需要复杂推理的业务场景仍可使用默认思考模式。

注意：

不要仅凭一次测试就认定 thinking mode 是根因，必须以重复实验统计结果为准。

5. P0：CandidateBatch Adapter 最小修复

5.1 优先修真实 bug

如果诊断发现：

raw / provider parsed 已经是正确结构

但项目 adapter 再次包装：

{"candidates": parsed}

则直接删除重复包装逻辑。

这是优先方案。

不要为了兼容自己的 bug 再添加复杂 normalization。

5.2 仅在 provider 确实存在稳定 wrapper 差异时做 canonicalization

如果确认 provider / SDK 确实稳定返回一层额外 wrapper，可在进入：

CandidateBatch.model_validate(...)

之前做一个非常窄的 normalization。

示意：

def normalize_candidate_batch_output(value):
    if value is None:
        raise InvalidModelOutputError("candidate output is empty")

    if isinstance(value, CandidateBatch):
        return value

    if not isinstance(value, dict):
        raise InvalidModelOutputError("candidate output must be an object")

    candidates = value.get("candidates")

    if isinstance(candidates, list):
        return value

    if (
        isinstance(candidates, dict)
        and set(candidates.keys()) == {"candidates"}
        and isinstance(candidates["candidates"], list)
    ):
        return {
            **value,
            "candidates": candidates["candidates"],
        }

    raise InvalidModelOutputError("candidate output schema is invalid")

限制：

只允许一层明确重复 wrapper

禁止：

递归 unwrap
自动猜 schema
自动修复任意 JSON
吞掉未知字段错误

6. P0：合法空 Candidate 必须成功

以下结果必须视为合法：

{
  "candidates": []
}

对应 Capture：

status = completed
auto_saved_count = 0
pending_count = 0
discarded_count = 0

适用场景包括：

继续
寒暄
纯操作说明
无长期价值的 assistant meta turn

合法 empty batch 绝不能进入：

invalid_candidate_output

7. P0：Retry 保持简单

保留当前最多 3 次 CandidateExtractor structured-output retry。

Retryable

provider 返回 None
JSON parse failure
schema malformed
structured output adapter failure

不 Retry

合法 candidates=[]

单条 Candidate source_expression 不合法
→ discard candidate

单条 Candidate Admission 进入 pending/discard
→ 正常继续

Relation policy mismatch / confidence below threshold
→ skip relation

不要因为某一条 Candidate 的业务验证失败，重新执行整个 CandidateExtractor。

8. P0：Structured Output 测试

至少增加以下单元测试。

正常结构

{"candidates": []}
→ success

{"candidates": [{...}]}
→ success

如果确认 provider 存在重复 wrapper

{"candidates": {"candidates": []}}
→ normalize → success

{"candidates": {"candidates": [{...}]}}
→ normalize → success

异常结构

None
→ retryable InvalidModelOutputError

{"candidates": "xxx"}
→ retryable InvalidModelOutputError

{"foo": []}
→ InvalidModelOutputError

CandidateBatch instance
→ success

另外增加真实 DeepSeek integration stability test：

固定 fixture 连续调用至少 30 次

统计成功率，而不是只测单次 happy path。

9. P1：测试环境物理隔离

9.1 E2E 前准备

每次正式验收前执行：

1. 关闭当前 Claude 会话

2. 找到当前项目对应：
   ~/.claude/projects/<project>/memory/

3. 备份并临时移走该目录

4. 确认测试项目根目录存在 CLAUDE.md

5. 使用 VS Code 重新打开项目根目录

6. 新建 Claude 会话

7. 确认测试过程中没有读取旧 Claude project memory

8. 清空 Memory MCP 当前测试 owner 的数据

如果方便，推荐直接使用一个全新的测试目录路径，例如：

hengchuan_memory_e2e_clean_v1/

避免 Claude Code 根据旧项目路径恢复历史项目 memory。

10. CLAUDE.md 建议内容

保持简短，不要写复杂策略。

# Memory Test Rules

This project uses Memory MCP for long-term memory testing.

- Do not use Claude's built-in MEMORY.md or project memory for long-term memory.
- Automatic recall and capture are handled by configured hooks.
- Do not call `capture_completed_turn` directly.
- Do not manually persist ordinary conversation content.
- Memory MCP management tools may only be used when the user explicitly asks to inspect or manage stored memories.

注意：

CLAUDE.md 只是辅助约束。

正式 E2E 的可信性应来自：

内部 Memory 物理隔离
+
日志验证

11. 本轮不再优化 Candidate Prompt

当前暂时不要继续增加：

更多 source_expression 规则
更多 assistant exclusion
更多案例
更多 atomicity 描述
更多 relation 描述

当前主要失败发生在：

structured output → CandidateBatch schema parsing

不是 Candidate semantic validation。

因此先修底层稳定性。

12. P2：Evidence Coverage 作为 Known Limitation

当前仍允许暂时存在：

content 包含多个 period 的总结
source_expression 只支持其中一部分

本轮不要为此实现：

Multi-Evidence
Evidence Graph
Cross-document Evidence Bundle
新的复杂 validator framework

继续依赖：

Prompt 原子化
+
source_expression containment validation

即可。

后续如需要，只增加更严格的测试和轻量 prompt 调整。

13. 修复后核心 E2E

Structured output 修好以后，不需要立刻重新跑完整 8 轮。

先从空库跑四个核心场景。

Test A：明确长期基准

输入用户明确的：

核心 thesis
风险
未来跟踪指标
分析偏好

验收：

Capture completed
invalid_candidate_output = false
auto_saved_count >= 3

至少应出现：

thesis
risk
ongoing_research
research_preference

Test B：全新会话 Recall

新建 Claude 会话，不读取任何历史材料或 Claude 内部 Memory。

输入：

我要继续跟踪恒川工业软件。
请基于我之前形成的研究判断，告诉我下一季度最值得验证的指标。

验收：

服务端：

candidate_count > 0
result_count > 0
rendered_context != ""

Agent：

recalled_count > 0

同时确认回答依赖的是 BeforeRun Recall 注入内容，而不是文件工具。

Test C：明确修正 thesis

输入明确修正：

我不再认为旧判断成立……
以后更核心的判断应该是……
请以后以这个更新后的判断为准。

验收：

Capture completed
invalid_candidate_output = false
new thesis auto-saved

如果 replacement 已稳定：

old thesis → superseded
new thesis → active/current

如果 replacement 仍有轻微限制，最低要求：

new thesis active
最终 Recall 优先返回新 thesis

Test D：最终新会话 Recall

再次创建全新 Claude 会话。

验收：

MCP result_count > 0
Agent recalled_count > 0

回答应优先使用更新后的长期判断。

同时日志中不得出现：

Read ~/.claude/projects/.../memory
模型手动 capture_completed_turn
模型无明确用户意图时主动 batch_confirm/revoke/link

14. 核心通过后再补两个增强测试

Uncertain

用户明确说只是猜测、证据不足

期望：

Capture completed
pending_count > 0
auto_saved_count = 0

Relation

用户明确表达：

某 evidence challenges 某 thesis

期望：

RelationExtractor 真正执行

合法关系：

accepted

普通 policy rejection / low confidence：

skipped
Capture completed

不得再次因为普通 Relation policy 问题回滚 Candidate Capture。

15. 本轮明确禁止修改

不要修改：

Relation threshold
Admission threshold
Recall similarity threshold
Memory types
Relation types
PostgreSQL schema
MCP contract
Agent retry architecture
Timeline
Team Memory
Evidence architecture

除非 structured-output 诊断能够直接证明其中某一项是根因，否则不要顺带调整。

16. Stop Condition

满足以下条件后停止修改 Core：

1. DeepSeek Candidate structured output 重复测试稳定
2. explicit user thesis 可稳定 auto-save
3. fresh session Recall 稳定命中
4. thesis update 不再因 CandidateBatch parsing 失败
5. uncertain hypothesis 稳定进入 Pending
6. Relation 普通 policy rejection 不影响 Capture
7. Claude 内部 Memory 不再污染 E2E

之后以下问题全部暂列 Known Limitations：

assistant extraction noise
Evidence Coverage 不完美
Capture 延迟 20～40 秒
复杂关系图
Timeline / multihop
Team Memory

不要继续扩展。

17. 最终执行顺序

按以下顺序完成：

Step 1
增加 structured-output failure diagnostics

Step 2
运行 deepseek-v4-pro thinking on/off A/B 稳定性实验

Step 3
定位 raw response → adapter → CandidateBatch 的真实错误层

Step 4
只修真实 adapter bug；必要时增加一层非常窄的 canonicalization

Step 5
补 structured-output unit/integration tests

Step 6
物理隔离 Claude project memory

Step 7
清空 Memory MCP 测试 owner 数据

Step 8
跑核心 E2E：A → B → C → D

Step 9
核心通过后补 uncertain + relation

Step 10
停止继续扩展 Core

最终判断原则：

如果一个修改不能直接提高 structured-output 稳定性、消除测试污染、或让核心 E2E 更可靠，本轮就不要做。