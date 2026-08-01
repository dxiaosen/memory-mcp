# Memory MCP 文档导航

第一次阅读建议按以下顺序：

1. [详细总设计](design.md)：产品边界、数据流、领域模型、关系与扩展机制；
2. [配置参考](config.md)：Server 与 Agent 的运行配置；
3. [Agent 主动记忆](agents.md)：通用 Hook 合同及 Codex、Claude Code 接入；
4. [端到端使用](usage.md)：安装、启动和手工验证；
5. [测试与验收](testing.md)：测试分层、命令和数据库安全边界；
6. [投研记忆评测](evaluation.md)：数据集、真实模型结果与失败分析；
7. [日志规范](logging.md)：日志字段、内容开关和脱敏边界；
8. [部署指南](deploy.md)：ECS/RDS、systemd、网络与回滚。

## 文档职责

| 文档 | 唯一职责 |
| --- | --- |
| `design.md` | 解释系统是什么以及为什么这样设计 |
| `config.md` | 列出可配置项、默认值和 Secret 边界 |
| `agents.md` | 说明 Agent 生命周期与宿主接入 |
| `usage.md` | 提供可执行的运行和手工验证步骤 |
| `testing.md` | 说明自动化测试、外部依赖和发布检查 |
| `evaluation.md` | 维护投研质量基准及结果 |
| `logging.md` | 规定可观测性与敏感信息处理 |
| `deploy.md` | 说明生产部署、升级和回滚 |

发行边界只有两个：`memory-mcp` 是 Server，`memory-mcp-agent` 是部署在 Agent
Host 上的轻量 Hook Client。开发环境可以同时安装二者，生产部署应独立安装。

OpenSpec 只管理需求、设计决策和实施进度，不作为第二套使用手册。变更依赖和状态
统一从 [OpenSpec 导航](../openspec/README.md) 查看；当前运行语义以
[详细总设计](design.md)为准。
