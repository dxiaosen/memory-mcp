# 投研记忆质量评估

`cases.json` 是版本化中文投研基准，当前包含 48 个案例：16 个候选、13 个关系、11 个召回和 8 个安全案例。它覆盖 `investment-research` 的八种记忆类型、六种有向关系、语义/报告期/实体/长期旧记忆召回干扰项，以及交易、持仓、凭据和正常研究文本边界。

默认离线评估不读取模型配置、不访问网络或数据库：

```bash
.venv/bin/python -m evals.runner
```

离线只评估生产召回排序和 SensitiveContentGuard；候选/关系指标为 `null`，不会把金标当预测。任一已评估指标未满足阈值时进程返回非零。

只有需要显式评估当前真实模型时才运行：

```bash
set -a
source server/.env
set +a
.venv/bin/python -m evals.runner \
  --live-model \
  --output evals/results/<safe-result-name>.json
```

真实模式复用 `MEMORY_MCP_MODEL_*`，候选和关系走当前 extractor 与进程内 Repository；召回使用 Profile 驱动的生产确定性排序，安全仍运行 SensitiveContentGuard。`--output` 的父目录必须已存在。标准输出和结果文件只包含数据集/模型/prompt/schema 标识、聚合/分类指标、耗时和失败 case ID，不包含案例正文、身份、数据库 URL 或 Secret。

一次已记录结果、失败解释和适用边界见[投研记忆评测报告](../docs/evaluation.md)。
