# Memory MCP 文档导航

本目录只保留面向当前系统的读者文档。第一次阅读建议按以下顺序：

1. [详细总设计](design.md)：从产品定位、数据流、领域模型一直读到部署和扩展；
2. [配置参考](config.md)：全部环境变量、默认值、Secret 和测试配置；
3. [Agent 主动记忆](agents.md)：通用合同、内置宿主配置和手工端到端验证；
4. [端到端使用](usage.md)：从空环境启动、真实模型、通用 Runner 与确定性测试；
5. [测试与验收](testing.md)：测试分层、真实 PostgreSQL/模型证据和故障矩阵；
6. [日志规范](logging.md)：允许记录和禁止记录的字段；
7. [部署指南](deploy.md)：systemd、RDS、私网和公网 HTTPS。

发行边界只有两个：`memory-mcp` 是 Server，`memory-mcp-agent` 是远端 Agent
Host 的轻量 Hook Client。仓库开发环境可以同时安装二者，生产部署不能据此把两端
当成一个“全家桶”。

## 文档职责

| 文件 | 回答的问题 |
| --- | --- |
| `design.md` | 系统是什么、为什么这样设计、各层如何协作 |
| `config.md` | 配置什么、默认值是什么、哪些是 Secret/测试值 |
| `agents.md` | 如何让任意 Agent 每轮自动召回和捕获 |
| `usage.md` | 如何启动、切换模型和使用通用 Runner |
| `testing.md` | 如何证明行为正确，哪些路径使用替身或外部服务 |
| `logging.md` | 可以记录什么，如何避免正文和 Secret 泄漏 |
| `deploy.md` | 如何发布到 ECS/RDS 和配置网络边界 |

同一内容只在一个文件维护；其他文档只链接，不复制完整表格或步骤。

## OpenSpec 职责

- [当前状态与归档顺序](../openspec/README.md)

OpenSpec 是规范与变更管理区，不是第二套使用手册：

| 制品 | 唯一职责 |
| --- | --- |
| `proposal.md` | 动机、范围和影响 |
| `specs/*/spec.md` | 规范性 MUST/SHALL 和验收场景 |
| `design.md` | 关键技术决策、替代方案和权衡 |
| `tasks.md` | 唯一实施进度 |

基础变更：

- [Proposal](../openspec/changes/add-general-memory-core/proposal.md)
- [OpenSpec Design](../openspec/changes/add-general-memory-core/design.md)
- [Tasks](../openspec/changes/add-general-memory-core/tasks.md)
- [Capture Spec](../openspec/changes/add-general-memory-core/specs/memory-capture/spec.md)
- [Governance Spec](../openspec/changes/add-general-memory-core/specs/memory-governance/spec.md)
- [Lifecycle Spec](../openspec/changes/add-general-memory-core/specs/memory-lifecycle/spec.md)
- [Recall Spec](../openspec/changes/add-general-memory-core/specs/memory-recall/spec.md)

主动记忆变更：

- [Proposal](../openspec/changes/add-agent-active-memory/proposal.md)
- [OpenSpec Design](../openspec/changes/add-agent-active-memory/design.md)
- [Tasks](../openspec/changes/add-agent-active-memory/tasks.md)
- [Agent Active Memory Spec](../openspec/changes/add-agent-active-memory/specs/agent-active-memory/spec.md)

通用元数据增强：

- [Proposal](../openspec/changes/enhance-memory-metadata/proposal.md)
- [OpenSpec Design](../openspec/changes/enhance-memory-metadata/design.md)
- [Tasks](../openspec/changes/enhance-memory-metadata/tasks.md)
- [Memory Metadata Spec](../openspec/changes/enhance-memory-metadata/specs/memory-metadata/spec.md)

投研 Profile：

- [Proposal](../openspec/changes/add-investment-research-profile/proposal.md)
- [OpenSpec Design](../openspec/changes/add-investment-research-profile/design.md)
- [Tasks](../openspec/changes/add-investment-research-profile/tasks.md)
- [Investment Research Spec](../openspec/changes/add-investment-research-profile/specs/investment-research-memory/spec.md)

通用记忆关系：

- [Proposal](../openspec/changes/add-memory-relations/proposal.md)
- [OpenSpec Design](../openspec/changes/add-memory-relations/design.md)
- [Tasks](../openspec/changes/add-memory-relations/tasks.md)
- [Memory Relations Spec](../openspec/changes/add-memory-relations/specs/memory-relations/spec.md)

自动记忆关系：

- [Proposal](../openspec/changes/automate-memory-relations/proposal.md)
- [OpenSpec Design](../openspec/changes/automate-memory-relations/design.md)
- [Tasks](../openspec/changes/automate-memory-relations/tasks.md)
- [Automatic Relations Spec](../openspec/changes/automate-memory-relations/specs/automatic-memory-relations/spec.md)

关系证据链与质量评估：

- [Proposal](../openspec/changes/harden-memory-relations/proposal.md)
- [OpenSpec Design](../openspec/changes/harden-memory-relations/design.md)
- [Tasks](../openspec/changes/harden-memory-relations/tasks.md)
- [Relation Provenance Spec](../openspec/changes/harden-memory-relations/specs/relation-provenance/spec.md)
- [Memory Quality Evaluation Spec](../openspec/changes/harden-memory-relations/specs/memory-quality-evaluation/spec.md)

上述目录是尚未归档的变更历史：`add-general-memory-core` 仍保留部署/现场交付任务，
其余变更的实现任务已经完成。完成状态只看各自 `tasks.md`，当前运行语义统一以
`docs/design.md` 为准；不因代码完成而伪造公网验收、现场脚本或录屏证据。

规范性需求以 capability specs 为准，完成状态只看 tasks，当前实现解释以
`docs/design.md` 为准。
