# 记忆质量评估

默认评估不读取模型配置、不访问网络或数据库：

```bash
.venv/bin/python -m evals.runner
```

它校验 `cases.json`，运行确定性候选/关系基线、当前文本召回排序和敏感守卫，并输出 candidate/relationship precision、recall、Recall@K 与 safety pass rate。任一阈值不满足时进程返回非零退出码。

只有需要显式评估当前真实模型时才运行：

```bash
set -a
source server/.env
set +a
.venv/bin/python -m evals.runner --live-model
```

真实模型模式复用 `MEMORY_MCP_MODEL_*`，使用进程内 Repository 执行候选准入和关系准入，不连接生产 PostgreSQL。标准输出只包含数据集版本、指标、计数和失败 case ID，不输出案例正文或凭据。
