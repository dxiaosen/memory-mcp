# 记忆质量评测

## 三种模式

| 模式 | 说明 | 默认 | 需外部 Provider |
| --- | --- | --- | --- |
| `deterministic` | 确定性 extractor/embedding，不调真实模型，CI 回归门禁 | ✅ | 否 |
| `live-extraction` | 真实 Chat Model 候选/关系抽取质量评测 | — | 需要 `MEMORY_MCP_MODEL_*` |
| `live-embedding` | 真实 EmbeddingProvider 词法 vs 向量召回比较 | — | 需要 `MEMORY_MCP_EMBEDDING_*` |

## 运行

```bash
# 确定性（默认，CI 门禁，不调模型）
uv run python -m evals.runner --mode deterministic

# 真实模型抽取
uv run python -m evals.runner --mode live-extraction --output evals/results/<name>.json

# 真实向量召回
uv run python -m evals.runner --mode live-embedding --output evals/results/<name>.json

# 按 suite/tag/case-id 过滤
uv run python -m evals.runner --suite recall --tag thesis
uv run python -m evals.runner --case-id recall-thesis-product-mix

# 更新 baseline（显式，普通运行不会修改）
uv run python -m evals.runner --mode deterministic --update-baseline

# Markdown 报告
uv run python -m evals.runner --mode deterministic --markdown evals/results/report.md
```

未配置 Provider 时，live 模式会明确 skip 对应案例并记录原因，不静默使用 Fake。

## 数据集

`memory-mcp-eval-v5-2026-08-05`，66 个案例（虚构公司/数据）。

| suite | 数量 | 模式 | 评估对象 |
| --- | ---: | --- | --- |
| capture-admission | 31 | live-extraction | 候选抽取（8 类投研记忆 + 2 类通用办公）+ 关系抽取（6 类合法关系）+ 负例 |
| recall | 20 | deterministic/live-embedding | 空结果/同义改写/报告期干扰/实体重名/长期旧记忆/大窗口/中英混合/英文/general-work |
| safety-isolation | 11 | deterministic | 凭据/持仓/交易指令 + 正常研究文本 + owner/team/MCP 注入隔离 |
| lifecycle | 4 | deterministic | duplicate/replacement/ambiguous/revoke |

| profile | 数量 | 语言 |
| --- | ---: | --- |
| investment-research | 61 | 中文为主 + 部分中英混合/英文 |
| general-work | 5 | 英文/中英混合 |

## 指标与门槛

| 指标 | 门槛 | 关注风险 |
| --- | ---: | --- |
| Candidate precision | 0.85 | 错误保存长期记忆 |
| Candidate recall | 0.80 | 漏掉有价值记忆 |
| Candidate F1 | — | P/R 综合 |
| Relation precision | 0.90 | 错误关系污染召回 |
| Relation recall | 0.75 | 漏掉明确关系 |
| Relation F1 | — | P/R 综合 |
| Recall@K | 0.80 | 新任务找不到历史上下文 |
| Precision@K | — | 召回精确性 |
| MRR | — | 召回排序质量 |
| Safety pass rate | 1.00 | 禁止内容漏拦或正常文本被误拦 |
| Isolation pass rate | 1.00 | 跨 owner/team 访问被拒绝 |
| Lifecycle pass rate | 1.00 | 生命周期状态转换正确 |

## 当前结果

| 指标 | deterministic | live-extraction | live-embedding |
| --- | ---: | ---: | ---: |
| Candidate P/R/F1 | — | 1.0/1.0/1.0 | — |
| Relation P/R/F1 | — | 1.0/1.0/1.0 | — |
| Recall@K | 1.0 (20/20) | — | 1.0 (20/20) |
| Precision@K | 1.0 | — | 1.0 |
| MRR | 1.0 | — | 0.933 |
| Safety pass rate | 1.0 (8/8) | 1.0 (8/8) | 1.0 (8/8) |
| Isolation pass rate | 1.0 (3/3) | — | — |
| Lifecycle pass rate | 1.0 (4/4) | — | — |
| thresholds_met | ✅ | ✅ | ✅ |

- live-extraction 使用 `deepseek:deepseek-v4-flash`；
- live-embedding 使用 `text-embedding-v3`；
- deterministic 连续两次运行结果完全一致。

## 筛选

```bash
--suite capture-admission|recall|safety-isolation|lifecycle
--tag thesis|risk|evidence_claim|english|mixed|...
--case-id <具体 case_id>
```

## baseline

三种模式各维护一个 baseline（`evals/baselines/<mode>.json`）：

- **deterministic**：强制回归门禁，质量回退时返回非零退出码；
- **live-extraction/live-embedding**：趋势比较，不作为 CI 强门禁。

普通运行不修改 baseline，更新必须显式 `--update-baseline`。

## 结果限制

- 66 个案例不具统计显著性，不是 SLA
- deterministic 的 Recall/Safety 是确定性实现结果，不是大模型指标
- live-extraction 的 Candidate/Relation 结果取决于模型能力
- 满分只说明当前案例通过，后续应扩充覆盖
- 不代表投资收益或生产吞吐
