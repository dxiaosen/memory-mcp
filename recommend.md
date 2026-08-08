# Memory MCP 本轮修复任务（二）

请基于当前实现做小范围修复，优先解决本轮联调日志中暴露出的 Turn 边界、Source 选择和 `source_expression` 校验问题。不要重构整体架构，也不要修改现有 Admission / Lifecycle 核心规则。

## 1. P0：CompletedTurnEvent 只包含当前 Turn

当前第二轮请求虽然用户只提交了新的长期研究判断，但 `capture_completed_turn` 中仍然重复包含了上一轮读取的 9 个 tool/document 消息。

请修复 Claude Code Host Adapter 的 Turn 截取逻辑。

目标：

```text
CompletedTurnEvent
=
当前 user prompt
+ 当前 prompt 之后产生的 tool calls / tool results
+ 当前 assistant response
```

不要发送整个 conversation transcript。

例如本轮没有新的 tool 调用时，应近似：

```text
message_count=2

[user]
当前长期研究判断

[assistant]
当前回复
```

要求：

- Turn 边界由 Agent Host Adapter 负责；
- Server 不感知 Claude Code transcript 结构；
- 保持 `CompletedTurnEventV1` 通用契约不变；
- 增加连续两轮测试，验证第二轮不会重复包含第一轮 tool message。

---

## 2. P0：用户原始表达优先于 Assistant 复述

同一语义同时出现在多个来源时，优先级：

```text
user explicit
>
tool/document original source
>
assistant paraphrase
```

尤其以下类型，只要用户原文中存在明确表达，应优先绑定用户原始消息：

- research_preference
- thesis
- research_decision
- risk
- ongoing_research
- 用户明确要求长期保存/作为基准的判断

示例：

```text
用户：
我认为 AI 热管理材料收入占比提升会推动利润结构改善。
```

应得到：

```text
source_role=user
assertion_kind=user_view
expression_basis=explicit
```

而不是绑定 Assistant 的复述为：

```text
source_role=assistant
assertion_kind=system_inference
```

符合现有 Admission 自动保存条件时，应正常进入 `auto_save`。

---

## 3. P0：`source_expression` 校验支持换行 / 空白归一化

当前真实存在于用户原文中的表达，因为换行或空格不同被判定：

```text
invalid_source_expression
```

请将原文匹配改为：

```text
normalize(source_expression)
in
normalize(source_message)
```

normalize 只允许：

- `\r\n` / `\r` / `\n` 统一为空格；
- 连续 whitespace 压缩成单个空格；
- trim 首尾空白。

不要做模糊语义匹配，不要改写字符内容。

目标：

```text
真实原文 + 仅格式差异
→ valid

模型自行拼接/改写多个独立句子
→ invalid
```

---

## 4. 保持对真正非法 source_expression 的严格拒绝

不要因为第 3 条而放松整个校验。

如果模型把多个独立 bullet 拼成新的句子，而原文没有这段连续表达，可以继续：

```text
invalid_source_expression
```

更推荐模型拆成多个原子 Candidate。

---

## 5. 加强 Candidate 原子化

避免一个 Candidate 同时混合：

- 多个外部来源；
- 外部事实；
- 研究推断；
- 研究阈值；
- 用户观点。

如果一个 Candidate 不能由单个 source span 完整支撑，优先拆成多个 Candidate。

暂时不要新增复杂多 Evidence 模型，优先通过 Prompt 和原子化解决。

---

## 6. 验证 research_preference 能正确保存

下面这类用户明确偏好：

```text
以后分析公司时，请使用中文；
按“事实—判断—风险—验证指标”结构输出；
关键数字使用表格；
管理层口径必须标记为待验证。
```

应抽取为：

```text
memory_type=research_preference
source_role=user
assertion_kind=user_view
expression_basis=explicit
durability=durable
```

如果 confidence 达到自动保存阈值，应进入：

```text
auto_save
```

不能再因为 Assistant 复述或换行问题变成 pending / discard。

---

## 7. 验证用户明确 thesis / risk / ongoing_research

请针对以下用户表达补测试：

```text
这是我的长期研究基准……
我认为……
我暂时不认为……
主要风险包括……
未来两个季度重点跟踪……
```

预期：

```text
thesis
→ user_view

risk
→ user_view

ongoing_research
→ user_view

research_preference
→ user_view
```

且优先绑定 user message。

最终至少部分应满足：

```text
decision=auto_save
```

而不是全部被：

```text
invalid_source_expression
system_inference
non_user_source
```

挡掉。

---

## 8. 保留现有已修好的行为

以下不要回退：

- `extracted_candidate_count`
- `validated_candidate_count`
- `outcome_count`
- validation rejected 完整日志
- Capture 分阶段耗时
- Recall 分阶段耗时
- zero-result `rendered_context=""`
- `memory.recall.candidates`
- Capture timeout / retry 修复
- workspace-relative `source_uri`
- tool/document provenance
- Assistant 推断使用 `system_inference + inferred`

---

## 9. 可顺手检查：Team Extraction 去重

当前服务启动后出现：

```text
team_count=3
```

但连续三次：

```text
team_owner_ref="tenant-001:team:research-dept"
```

如果实际只有一个 team，请检查是否按成员重复产生同一个 team owner，并在 batch 前按 `team_owner_id` 去重。

此项优先级低于前 3 项。

---

## 验收标准

重新执行两轮测试。

### 第一轮：读取材料

允许：

```text
tool/document facts → pending
assistant inference → pending
```

不要求 auto-save。

### 第二轮：用户明确长期研究基准

若本轮无工具调用，应看到近似：

```text
message_count=2
```

并应出现：

```text
source_role=user
assertion_kind=user_view
expression_basis=explicit
```

覆盖：

- thesis
- risk
- ongoing_research
- research_preference

至少部分满足自动保存条件时：

```text
auto_saved_count > 0
```

同时：

```text
invalid_source_expression
```

不应再因为纯换行 / 空白差异误杀用户原文。

## 约束

- 不修改当前开发阶段完整内容日志策略；
- 不做日志脱敏调整；
- 不修改 Admission 主规则；
- 不修改 Lifecycle 主规则；
- 不新增复杂 salience pruning；
- 不让 Server 依赖 Claude Code transcript；
- 保持 MCP 接口和 DTO 向后兼容；
- 优先小改动；
- 补充对应单元测试和至少一个两轮 E2E 测试。