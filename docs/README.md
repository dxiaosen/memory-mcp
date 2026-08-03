# Memory MCP 文档导航

第一次阅读建议按以下顺序：

1. [详细总设计](design.md)：产品边界、数据流、领域模型、关系与扩展机制；
2. [配置参考](config.md)：Server 与 Agent 的运行配置；
3. [Agent 主动记忆](agents.md)：通用 Hook 合同及 Codex、Claude Code 接入；
4. [端到端使用](usage.md)：开发环境上手、真实模型闭环和故障排查；
5. [测试与验收](testing.md)：测试分层、命令和数据库安全边界；
6. [投研记忆评测](evaluation.md)：数据集、真实模型结果与失败分析；
7. [日志规范](logging.md)：日志字段、内容开关和脱敏边界；
8. [部署指南](deploy.md)：生产 ECS/RDS、systemd、网络与回滚。

## 文档职责

| 文档 | 唯一职责 | 读者 |
| --- | --- | --- |
| `design.md` | 系统是什么、为什么这样设计 | 开发者、评审 |
| `config.md` | 所有配置项、默认值和 Secret 边界 | 运维、开发者 |
| `agents.md` | Agent 生命周期与宿主接入 | Agent 接入方 |
| `usage.md` | 开发环境上手和手工验证 | 开发者 |
| `testing.md` | 测试分层、命令和发布检查 | 开发者、CI |
| `evaluation.md` | 投研质量基准及结果 | 产品、模型 |
| `logging.md` | 日志字段与脱敏边界 | 运维 |
| `deploy.md` | 生产部署、升级和回滚 | 运维 |

`usage.md` 面向开发环境上手，`deploy.md` 面向生产部署——两者有意有少量重叠
（安装和启动命令），但各自补充不同上下文。

发行边界只有两个：`memory-mcp` 是 Server，`memory-mcp-agent` 是部署在 Agent
Host 上的轻量 Hook Client。开发环境可以同时安装二者，生产部署应独立安装。

OpenSpec 只管理需求、设计决策和实施进度，不作为第二套使用手册。变更依赖和状态
统一从 [OpenSpec 导航](../openspec/README.md) 查看；当前运行语义以
[详细总设计](design.md)为准。
