# Design: source_expression Markdown 强调标记剥离问题修复

## 背景

实测日志显示候选抽取有约 47% 的 `invalid_source_expression` 丢弃率。根因：模型在生成 `source_expression` 时会剥离 Markdown 强调标记（`**`、`` ` ``、`_`、`~~`），而 `source_expression_matches` 当时只做三级归一化（原文/空白归一/compact 归一），都不剥离标记，导致诸如：

- 原文 `**库存周转天数有了硬天花板**`，模型产出 `库存周转天数有了硬天花板` → 三级全部 False → 丢弃。

对真实日志中 2 条被丢弃的 `source_expression` 复核：剥离标记后第 4 级归一化均为 True。

## 设计决策

### 决策 A：第 4 级归一化——剥离 Markdown 强调标记后 compact 比较

新增 `source_expression_matches` 第 4 层：对 `source_expression` 与 `source` 各自剥离 Markdown 强调标记（正则 `[*_~`]+` 全局替换为空），再做 compact 归一化比较。

**为何只剥离强调标记、不做更宽的 Markdown 解析：**

1. 强调标记是纯渲染装饰，语义零贡献，剥离不会改变内容语义；标题/链接/列表等结构性 Markdown 若被模型剥离，往往伴随内容改写（如链接文本与 URL 拆分），应靠"substantive rewrite 仍被拒"来挡。
2. 正则 `[*_~`]+` 覆盖 `**`、`***`、`_`、`` ` ``、`~~` 等所有成对强调变体，简单且无副作用。
3. 强调标记剥离后再走 compact（去空白+去标点+小写），已能挡住"换词/换数字/换标点"类改写——这些改写不会因标记剥离而变得可匹配。

**风险与边界：**

- 若模型把链接 `[文本](url)` 剥成 `文本 url` 或 `文本`，第 4 级不保证匹配——但这属于改写而非纯标记剥离，应被拒。实测日志中此类情况未见（模型对链接一般保留 `[文本](url)` 原样或整段省略）。
- 若原文与模型产出在"是否有强调标记"上一致（都剥了或都留了），前 3 级已命中，第 4 级是兜底，不改变既有行为。

### 决策 B：Prompt 明确要求逐字保留

仅靠第 4 级兜底是被动的——模型仍会剥离标记，只是不再被丢弃。因此在候选抽取与关系抽取两处 prompt 中加入明确指令：

1. **候选抽取 prompt**：`source_expression` 必须是原文的逐字连续子串，保留每一个字符（含标点、数字、Markdown 强调标记 `**`、`_`、`` ` ``、`~~`），不得清洗、改写或剥离格式。
2. **关系抽取 prompt**：同上逐字要求 + 明确禁止把 endpoint memory 的存储 `content` 当作 `source_expression`（关系来源是 source_turn，不是已存储记忆内容）。

**为何 prompt 也要改：**

- 第 4 级让"剥离了标记的产出"能通过，但理想是让模型不剥离——减少歧义、让前 3 级命中（更快、更严格）。
- 关系抽取的 source_expression 用存储 content 是另一独立 bug：模型从已存储记忆的 `content` 字段取文本当 source_expression，该文本已与 source_turn 不同（可能被抽取改写过），三级归一化常不命中。Prompt 明确禁止 + 逐字要求，从源头纠正。

### 不改的部分

- **schema/数据模型**：`source_expression` 仍是 `str`，无需迁移。
- **Profile 指纹**：`source_expression_matches` 在 `core/domain/lifecycle.py`，是纯函数，不影响 Profile payload，指纹不变。
- **召回打分常量**：不涉及。

## 影响面

| 文件 | 改动 |
| --- | --- |
| `core/domain/lifecycle.py` | `source_expression_matches` +第 4 层 + `_strip_markdown_emphasis` + docstring |
| `extraction/backends.py` | 候选 prompt A 段 + 关系 prompt 加逐字/禁用存储 content 指令 |
| `tests/integration/test_capture_service.py` | +3 测试（剥离标记通过/带链接通过/实质改写仍拒） |
