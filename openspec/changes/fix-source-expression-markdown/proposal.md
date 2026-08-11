# 修复 source_expression 校验对 Markdown 强调标记过敏

## 背景

生产日志显示约 47% 的候选因 `invalid_source_expression` 被丢弃，其中多数是本该沉淀的高价值判断更新
（如"库存周转 ≤50 天新阈值""Q2 不达标""Q3 三项全过条件"）。根因：模型提取 source_expression 时
常剥离 Markdown 强调标记（把 `**库存周转天数有了硬天花板**` 写成 `库存周转天数有了硬天花板`），
而 `source_expression_matches` 的三级空白归一化 containment 不容忍星号差异，逐字比对失败即整条丢弃。

同一根因也影响关系抽取：14:20 那轮 inspect turn 因 source_expression 失败连续重试 3 次全失败
（`relation_extraction_failed`），浪费 ~7 秒且无关系产出。其中模型还把已存储记忆的 content 当
source_expression（非本轮原文），是更严重的来源误用。

## 提议

- **A. 校验放宽（兜底）**：`source_expression_matches` 加第四级归一化——移除成对的 Markdown 强调/代码/
  删除线标记字符（`*`/`_`/`` ` ``/`~`）后再做 compact containment。只放过装饰层标记，不放过实质改写
  （增删字词、改标点、换数字在剥标记后仍不匹配）。
- **B. prompt 引导（治本）**：候选与关系抽取 prompt 显式要求 source_expression 逐字保留原文所有字符
  （含标点、数字、Markdown 标记），不得清洁/改写/剥格式。关系 prompt 额外禁止用端点记忆的存储 content
  作 source_expression（必须来自 source_turn）。

## 影响

- 仅改 `lifecycle.source_expression_matches`（纯函数，PG/in_memory/relation 共用）与抽取 prompt。
- 无 schema 变更、无指纹变化。
- A 兜底：即使模型继续剥星号也不误丢；B 引导：减少剥离行为。
