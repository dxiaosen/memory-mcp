# Memory MCP 文档导航

本目录只保留面向当前系统的读者文档。第一次阅读建议按以下顺序：

1. [详细总设计](design.md)：从产品定位、数据流、领域模型一直读到部署和扩展；
2. [配置参考](config.md)：全部环境变量、默认值、Secret 和测试配置；
3. [端到端使用](usage.md)：从空环境启动、fixed/真实模型、Agent Hook 接入；
4. [测试与验收](testing.md)：测试分层、真实 PostgreSQL/模型证据和故障矩阵；
5. [日志规范](logging.md)：允许记录和禁止记录的字段；
6. [部署指南](deploy.md)：systemd、RDS、私网和公网 HTTPS。

## 文档职责

| 文件 | 回答的问题 |
| --- | --- |
| `design.md` | 系统是什么、为什么这样设计、各层如何协作 |
| `config.md` | 配置什么、默认值是什么、哪些是 Secret/测试值 |
| `usage.md` | 如何启动、切换模型和接入真实 Agent |
| `testing.md` | 如何证明行为正确，哪些路径使用替身或外部服务 |
| `logging.md` | 可以记录什么，如何避免正文和 Secret 泄漏 |
| `deploy.md` | 如何发布到 ECS/RDS 和配置网络边界 |

同一内容只在一个文件维护；其他文档只链接，不复制完整表格或步骤。

## OpenSpec 职责

OpenSpec 是规范与变更管理区，不是第二套使用手册：

| 制品 | 唯一职责 |
| --- | --- |
| `proposal.md` | 动机、范围和影响 |
| `specs/*/spec.md` | 规范性 MUST/SHALL 和验收场景 |
| `design.md` | 关键技术决策、替代方案和权衡 |
| `tasks.md` | 唯一实施进度 |

当前变更：

- [Proposal](../openspec/changes/add-general-memory-core/proposal.md)
- [OpenSpec Design](../openspec/changes/add-general-memory-core/design.md)
- [Tasks](../openspec/changes/add-general-memory-core/tasks.md)
- [Capture Spec](../openspec/changes/add-general-memory-core/specs/memory-capture/spec.md)
- [Governance Spec](../openspec/changes/add-general-memory-core/specs/memory-governance/spec.md)
- [Lifecycle Spec](../openspec/changes/add-general-memory-core/specs/memory-lifecycle/spec.md)
- [Recall Spec](../openspec/changes/add-general-memory-core/specs/memory-recall/spec.md)

规范性需求以 capability specs 为准，完成状态只看 tasks，当前实现解释以
`docs/design.md` 为准。
