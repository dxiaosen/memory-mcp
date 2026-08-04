# 投研记忆评测

## 数据集

`investment-memory-v4-2026-08-02`，52 个中文案例（虚构公司/数据）。

| 任务 | 数量 | 评估对象 | 执行方式 |
|---|---:|---|---|
| Candidate | 16 | 八类投研记忆 + 临时/推断/禁止负例 | 仅 `--live-model` |
| Relation | 13 | 六类合法关系 + 方向/否定/角色负例 | 仅 `--live-model` |
| Recall | 15 | 空结果/同义改写/报告期干扰/长期旧记忆/大窗口 | 生产 `recall_memory` |
| Safety | 8 | 凭据/持仓/交易指令 + 正常研究文本 | 生产 `SensitiveContentGuard` |

## 指标与门槛

| 指标 | 门槛 | 关注风险 |
|---|---:|---|
| Candidate precision | 0.85 | 错误保存长期记忆 |
| Candidate recall | 0.80 | 漏掉有价值记忆 |
| Relation precision | 0.90 | 错误关系污染召回 |
| Relation recall | 0.75 | 漏掉明确关系 |
| Recall@K | 0.80 | 新任务找不到历史上下文 |
| Safety pass rate | 1.00 | 禁止内容漏拦或正常文本被误拦 |

## 运行

```bash
# 离线（默认，不调模型，只测 Recall + Safety）
.venv/bin/python -m evals.runner

# 真实模型
.venv/bin/python -m evals.runner --live-model --output evals/results/<name>.json
```

## 当前结果

| 指标 | 值 | 状态 |
|---|---:|---|
| Recall@K | 1.00 (15/15) | ✅ 通过 |
| Safety pass rate | 1.00 (8/8) | ✅ 通过 |
| Candidate | 1.00/1.00 (v2 快照) | ✅ 通过 |
| Relation | 1.00/1.00 (v2 快照) | ✅ 通过 |

首轮四个失败入口已全部修复并保留在数据集中：

| Case | 原问题 | 修复方式 |
|---|---|---|
| relation-invalid-direction | 模型改写反向表述 | 完整子句证据 + cue 方向校验 |
| relation-negated-support | "不能支持"被误识别 | 否定证据拒绝 |
| recall-ongoing-channel | "下一步"未优先 | Profile recall hints |
| recall-research-scope | "最终怎么定"未区分 | Profile recall hints |

## 结果限制

- 52 个案例不具统计显著性，不是 SLA
- Recall/Safety 是确定性实现结果，不是大模型指标
- 满分只说明当前案例通过，后续应扩充覆盖
- 不代表投资收益或生产吞吐
