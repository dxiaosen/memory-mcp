# Memory MCP 文档导航

## 快速入口

| 我想... | 看这里 |
|---|---|
| 了解系统设计 | [design.md](design.md) — 架构、流程图、约束 |
| 查配置项 | [config.md](config.md) — 环境变量、默认值 |
| 接入 Agent | [agents.md](agents.md) — Hook 合同、Codex/Claude Code |
| 本地跑通 | [usage.md](usage.md) — 安装、启动、手工验证 |
| 跑测试 | [testing.md](testing.md) — 分层、命令、DB 安全 |
| 看评测结果 | [evaluation.md](evaluation.md) — 52 case、指标 |
| 查日志 | [logging.md](logging.md) — 事件、字段、脱敏 |
| 生产部署 | [deploy.md](deploy.md) — ECS/RDS、systemd |

## 架构速览

```text
Agent Host (memory-mcp-agent)
  │ BeforeRun → recall_memory
  │ AfterRun  → capture_completed_turn
  ▼
Memory MCP Server (memory-mcp)
  ├── 认证 / 敏感检查 / 准入 / 生命周期
  ├── DeepSeek 结构化抽取
  └── PostgreSQL（唯一权威存储）
```

两个发行包：`memory-mcp`（Server）和 `memory-mcp-agent`（轻量 Client）。
生产独立安装，开发环境可同一仓库 `uv sync --all-packages`。

OpenSpec 管变更历史和需求，不作为使用手册：[OpenSpec 导航](../openspec/README.md)。
