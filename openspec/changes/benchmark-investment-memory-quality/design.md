## Context

`harden-memory-relations` 已提供 `evals/cases.json`、严格 Pydantic schema、确定性指标和显式 `--live-model`。原有候选/关系离线 baseline 直接使用金标，只能测试计分器却显示为 1.0，不能作为质量证据。investment v2 首轮真实运行进一步暴露了关系否定/方向误判和 Profile 召回语义不足，需要同时修正评测真实性与生产能力。

本变更面向研究原型的可解释评测。评估可使用部署者主动提供的 `server/.env` 模型配置，但不得连接 PostgreSQL、读取 owner 数据或把案例正文/Secret 写入结果。生产 Memory Core、Agent 和 MCP 契约保持不变。

## Goals / Non-Goals

**Goals:**

- 建立中文投研专用、正负例平衡、可版本化的候选/关系/召回/安全案例集。
- 让报告既有 micro precision/recall，也能按稳定业务类别看到失败分布。
- 明确区分真实模型评估任务与确定性程序评估任务。
- 输出可安全提交的运行元数据和聚合结果，并在文档中保存一次真实运行快照。

**Non-Goals:**

- 不把一次模型运行结果变成永不变化的单元测试金标。
- 不访问生产 PostgreSQL，不执行真实 Agent Hook，不评估最终投资建议正确性。
- 不声称小样本等于统计显著性、SLA、事实核验或投资收益评价。
- 不修改数据库 schema、MCP 工具、Agent 协议、运行配置或既有身份隔离边界。
- 不针对单个 case ID 写特例，不用放宽金标或删除生产回归测试来提高分数。

## Decisions

### 1. 用一个 investment v2 数据集覆盖四个评估层面

数据集继续使用严格 JSON 合同，但每个 case 增加 `category`。案例统一围绕财务质量、盈利能力、竞争风险、资本开支、催化剂、研究缺口、研究范围和来源安全，覆盖：

- 八种 `investment-research` memory type 的显式长期陈述与容易误存的负例；
- `supports/challenges/threatens/could_catalyze/addresses/resolves` 六种关系及方向、角色、歧义负例；
- 带近义表达、报告期/实体干扰项的 Recall@K；
- 交易指令、真实持仓、凭据、提示注入和正常投研文本。

拒绝加入行情预测正确率或投资收益，因为 Memory MCP 评估的是记忆行为，不是投资模型。

### 2. 真实模型只替换候选与关系预测

`--live-model` 继续调用当前 CandidateExtractor 和 RelationExtractor，并经过现有 InMemory/Core 准入边界；召回使用生产确定性 `_text_relevance`，安全使用生产 SensitiveContentGuard。报告显式列出：

```text
model_tasks = candidate, relation
deterministic_tasks = recall, safety
```

这避免把 Recall@K 或安全通过率误写成模型能力。拒绝连接生产数据库做评估，因为样本和生产 owner 数据必须隔离。

### 3. 报告增加分类结果和可复现运行元数据

报告新增每个 category 的 case 数、失败数和通过率；CLI 包装结果新增：

- mode、model_id；
- candidate/relation prompt 与 schema version；
- dataset version 和 SHA-256；
- duration、UTC 时间；
- model/deterministic task 列表。

输出只含 case ID 和聚合数字。`--output` 显式指定时才写 JSON 文件；父目录必须已存在，避免 runner 隐式创建任意目录。

### 4. 离线门禁与真实运行快照分离

默认 runner 不读取模型配置、不访问网络，只计分确定性的 recall/safety。candidate/relation 返回 `null` 且分类显示零个已评测案例，避免把金标回放伪装成离线质量。真实模型结果保存为带日期、数据集和模型标识的快照，同时在 `docs/evaluation.md` 解释结果和限制。真实结果不作为 pytest 固定断言，以免 provider 漂移令普通测试不稳定。

### 5. 关系抽取采用提示约束与确定性否定保护

关系 prompt 和结构化字段说明要求 `source_expression` 是同时含有 source/target 可识别表达的最小完整子句，而不是单独的关系动词；证据不足时不建边。prompt 同时禁止为适配合法方向而反转端点，也禁止把“不支持”重解释为其他关系。关系准入在模型输出之后增加三项保守保护：两端任一缺少最小文本证据时拒绝；明确否定关系动词时拒绝；Profile 可为关系声明 `direction_cues`，Core 比较 cue 两侧对 source/target 端点文本的最长匹配，只有反向证据显著强于正向证据时拒绝。没有 cue 时不做方向猜测。三项保护都不根据 case ID、具体公司名或投研 memory type 判断。

### 6. 召回语义由 Profile 声明

`MemoryProfile` 为各 memory type 提供可选 `recall_hints`。Core 只识别“查询是否命中该类型的语义提示”并给予有限加分；投研 Profile 声明“下一步/跟进”对应 ongoing research、“决定/最终”对应 research decision 等提示，通用 Profile 可提供自己的词表。这样修复投研排序问题而不让 Core 依赖投研类型。

### 7. 只保留验证产品行为的评测测试

删除案例中的 baseline 字段以及 runner 的测试专用 predictor 注入。pytest 保留严格 schema、覆盖范围、离线不初始化 provider、安全输出、缺失真实配置失败和核心计分行为；真实 provider 调用由显式 benchmark 命令留存证据，不用 mock 重演一遍。详细结果只维护在 `docs/evaluation.md`。

## Risks / Trade-offs

- **[模型输出具有波动]** → temperature 保持现有配置；记录模型/prompt/schema/时间，不把单次分数描述成稳定 SLA。
- **[案例过于容易导致虚高]** → 加入近义召回、相邻概念、错误方向、Assistant-only、短期行情和模糊关系负例，并公开失败 ID。
- **[案例正文进入结果]** → JSON 结果仅包含 ID、计数、版本和安全模型标识；文档只概述类别，不复制失败案例全文。
- **[一次批量模型调用中断]** → runner 保持失败可见并返回非零；只有完整完成的结果才写入正式快照。
- **[真实模型分数低于阈值]** → 如实记录，不修改金标迎合结果；后续单独调整 prompt/模型后用新快照比较。
- **[语义提示过度提升类型]** → 提示词由 Profile 管理且只提供有限加分，仍与文本、优先级、时间和置信度共同计分。
- **[否定规则误伤复杂表达]** → 仅匹配紧邻关系动词的明确否定，并保留模型与人工可见的失败案例用于迭代。

## Migration Plan

1. 删除 baseline 和测试 predictor，使离线报告如实标记未评测任务。
2. 增强关系否定/方向约束和 Profile 驱动召回，并用确定性回归用例验证。
3. 精简评测测试与重复文档，运行离线门禁。
4. 显式加载 `server/.env` 重新运行真实模型评测并更新安全快照。
5. 验证评测代码不进入 Server/Agent wheel；无需数据库 migration 或应用回滚。

## Open Questions

本轮不阻塞。后续可在积累人工标注后增加多次重复运行、置信区间以及“无记忆/朴素摘要/主动记忆”的任务结果对照。
