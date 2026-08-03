# 投研跨会话记忆评测

本文是投研记忆评测方法和当前结果的唯一维护位置。它回答“模型能否提取值得跨会话
保存的研究记忆、能否保守建立关系、召回能否找回相关内容”，不评价投资观点是否
正确，也不生成投资建议。

## 1. 数据集与边界

当前数据集 `investment-memory-v4-2026-08-02` 使用虚构公司和虚构数据，共 52 个中文案例：

| 任务 | 数量 | 评估对象 | 执行方式 |
| --- | ---: | --- | --- |
| Candidate | 16 | 八类投研记忆、临时/推断/禁止负例 | 仅 `--live-model` 使用模型 |
| Relation | 13 | 六类合法关系、方向/否定/角色/歧义负例 | 仅 `--live-model` 使用模型和准入保护 |
| Recall | 15 | 空结果、同义改写、报告期/实体强干扰、长期旧记忆和 101 条窗口 | 公开 `MemoryService.recall_memory` 生产链路 |
| Safety | 8 | 凭据、持仓、交易指令和正常研究文本 | 生产 SensitiveContentGuard |

候选覆盖 `research_preference`、`research_question`、`thesis`、
`evidence_claim`、`risk`、`catalyst`、`ongoing_research` 和
`research_decision`。关系覆盖 `supports`、`challenges`、`threatens`、
`could_catalyze`、`addresses` 和 `resolves`。

每个案例只有稳定 ID、category、输入夹具和金标；严格 schema 禁止额外 owner、
tenant、Token 等字段。评测用 InMemory Repository 建立正式领域记录，但 Recall 的
阈值、生命周期、关系扩展、预算裁剪和最终排序全部复用生产 Application Service，
不导入私有打分函数。真实模型只用于 Candidate/Relation，不连接 PostgreSQL，也不
读取运行用户数据。结果文件不保存案例正文或 provider 异常文本。

## 2. 指标与门槛

| 指标 | 门槛 | 关注风险 |
| --- | ---: | --- |
| Candidate precision | 0.85 | 错误保存长期记忆 |
| Candidate recall | 0.80 | 漏掉有价值记忆 |
| Relation precision | 0.90 | 错误关系污染召回和解释 |
| Relation recall | 0.75 | 漏掉明确关系 |
| Recall@K | 0.80 | 新任务找不到历史上下文 |
| Safety pass rate | 1.00 | 禁止内容漏拦或正常研究被误拦 |

关系 precision 高于 recall，因为漏建关系只损失辅助信息，误建关系可能错误解释论点
与证据方向。

## 3. 运行方式

离线门禁：

```bash
.venv/bin/python -m evals.runner
```

离线只计分 Recall 和 Safety。Candidate/Relation 输出 `null`，相关分类的
`evaluated_count=0`、`pass_rate=null`；项目不再用金标 baseline 生成虚假的 1.0。

真实模型：

```bash
set -a
source server/.env
set +a
.venv/bin/python -m evals.runner \
  --live-model \
  --output evals/results/investment-memory-v4-<model>-<date>.json
```

历史 v2 完整安全快照见
[`evals/results/investment-memory-v2-deepseek-v4-flash-2026-08-01.json`](../evals/results/investment-memory-v2-deepseek-v4-flash-2026-08-01.json)。

## 4. 本轮质量改进

- 删除 candidate/relation 金标 baseline 和测试专用 live predictor；真实 provider 质量
  只通过显式 benchmark 留证。
- 关系 schema/prompt v3 要求 `source_expression` 是同时包含 source/target 可识别表达
  的完整子句，单独的“支持”等关系词不再是有效证据。
- 关系准入拒绝明确否定表达，并使用 Profile 声明的 `direction_cues` 保守识别明显
  反向证据；Core 不依赖公司名、case ID 或投研 memory type。
- `MemoryProfile.recall_hints` 声明各类型的查询语义，Core 只提供有界加分，并继续结合
  文本相关性、类型优先级、置信度和时间排序。
- Recall 评测改为调用公开生产服务；低于阈值的查询会保留真实空结果，不再强制取
  原始分数 top-k。
- 新增长期旧记忆案例：目标比近期干扰项早 720 天且位于旧 recent-only 窗口之外；
  当前 trigram/近期混合候选在总上限为 3 时仍能找回目标。

这些改动针对的是首轮暴露出的通用缺陷，没有修改金标、降低门槛或删除失败案例。

### 4.1 中文分词与打分校准（2026-08-03）

本轮在召回打分链路引入可注入的中文分词，并校准影响召回结果和渲染预算的数值常量：

- `_text_relevance` 的 word overlap 此前用 `re.compile(r"\w+", re.UNICODE)` 切词，
  Python 的 `\w` 在 Unicode 模式下把无空格分隔的 CJK 连续文本当作单个 token，
  导致中文 word overlap 信号失效；投研场景改用注入 jieba 精确模式（关闭 HMM 以
  保证离线确定性），纯标点 token 被丢弃，避免 `zxqv-unique-778899` 与
  `alpha-research` 这类连字符文本产生虚假重叠。
- subject 精确命中加成从 `0.45` 下调到 `0.2`。原值使 subject 命中但正文无关的记忆
  clamp 到 1.0，压过正文高度相关的记忆；下调后正文相关度仍有话语权。
- token 估算从单一 `len/3` 改为按字符类别：CJK 约 1 token/字，ASCII 约 1 token/4
  字符。原值对 30 字中文估算 10 token（实际约 30 token），导致 `token_budget` 实际
  塞入远超预算的中文内容。

改造前后离线确定性评测对比：

| 指标 | 改造前 | 改造后 | 门槛 | 状态 |
| --- | ---: | ---: | ---: | --- |
| Recall@K | 1.00 | 1.00 | 0.80 | 通过 |
| Safety pass rate | 1.00 | 1.00 | 1.00 | 通过 |
| failed_case_ids | 空 | 空 | — | 通过 |

当前基准 `recall_at_k=1.0`，改造为预防性加固（激活此前失效的中文 word overlap 信号）
并为更大规模真实数据做准备，未降低门槛或删除案例。是否引入向量/embedding 由后续
真实投研失败样本和容量数据决定，本轮明确不预设答案。

## 5. 历史模型快照与当前确定性结果

### 5.1 2026-08-01 v2 真实模型快照

运行标识：

| 字段 | 值 |
| --- | --- |
| mode | `live-model` |
| model | `deepseek:deepseek-v4-flash` |
| candidate prompt/schema | `general-memory-extraction-v1` / `candidate-v1` |
| relation prompt/schema | `memory-relation-extraction-v3` / `relation-v1` |
| dataset SHA-256 | `73b2298dcca910f90e24fb6f3e7fa4219398644529150eaf838b6aa71d4d7dcb` |
| duration | 43.066 秒 |

聚合结果：

| 指标 | 首轮 | 最终 | 门槛 | 状态 |
| --- | ---: | ---: | ---: | --- |
| Candidate precision | 1.00 | 1.00 | 0.85 | 通过 |
| Candidate recall | 1.00 | 1.00 | 0.80 | 通过 |
| Relation precision | 0.80 | 1.00 | 0.90 | 通过 |
| Relation recall | 1.00 | 1.00 | 0.75 | 通过 |
| Recall@K | 0.80 | 1.00 | 0.80 | 通过 |
| Safety pass rate | 1.00 | 1.00 | 1.00 | 通过 |

该文件是 v2 数据集的真实模型快照；Candidate/Relation 的模型、Prompt 和 schema
未在本次维护/检索变更中改变，因此没有为了确定性 Recall 改造重复调用模型或覆盖
历史快照。

快照最终 `thresholds_met=true`。候选为 10 TP、0 FP、0 FN；关系为 8 TP、0 FP、
0 FN。11 个 category 的通过率均为 1.0，`failed_case_ids` 为空。

首轮四个失败入口均已保留在数据集中并通过：

| Case ID | 原问题 | 当前保护 |
| --- | --- | --- |
| `relation-invalid-direction-negative` | 模型把反向表述改写成合法方向 | 完整子句证据 + cue 方向校验 |
| `relation-negated-support-negative` | “不能支持”被识别为 supports | 否定证据拒绝 |
| `recall-ongoing-channel-work` | “下一步”未优先 ongoing research | Profile recall hints |
| `recall-research-scope-decision` | “最终怎么定”未区分决定和问题 | Profile recall hints |

### 5.2 2026-08-02 v4 离线门禁

| 字段 | 值 |
| --- | --- |
| mode | `offline` |
| dataset | `investment-memory-v4-2026-08-02` |
| dataset SHA-256 | `1a2179b8fa3617c5d4c79bef37b1ad07c72eff92eda4690cf5f65e0b20cd8d5e` |
| Recall cases | 15 |
| Safety cases | 8 |
| Recall@K | 1.00 |
| Safety pass rate | 1.00 |
| long-horizon-recall | 1/1 |
| empty / paraphrase / hard-negative / large-window | 各 1/1 |
| failed_case_ids | 空 |
| thresholds_met | `true` |

旧 recent-only 算法在 candidate limit 为 3 时只会看到三个最新干扰项，720 天前目标的
候选命中为 0/1；当前混合候选命中为 1/1。新增大窗口包含 1 条 1000 天前目标和 100
条近期干扰，在 candidate limit 为 10 时仍命中目标。同义改写、零命中和报告期/近名
实体强干扰也全部通过。因此当前证据不支持在 BeforeRun 热路径增加一次模型调用；
后续若真实失败集出现词法无法覆盖的查询，再评估 query expansion、Embedding 或
可选模型 rerank。这里的通过不代表 Embedding 级语义能力。

## 6. 结果限制

- 当前 v4 是 52 个案例；真实模型分数仍来自 47 案例的 v2 单模型单次快照，不具备
  统计显著性，也不是 SLA；
- Recall 和 Safety 是确定性实现的结果，不是大模型指标；
- 候选 precision/recall 不代表记忆内容在现实世界中为真；
- 关系 recall 只覆盖显式一跳关系，不代表知识图谱或多跳推理质量；
- 满分只说明当前固定案例全部通过，后续仍应扩充表达、行业和模型覆盖；
- 结果不代表投资收益、投资建议质量、生产吞吐或 P95 延迟。
