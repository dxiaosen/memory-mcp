## Context

当前仓库分为 Server、轻量 Agent、开发评测和测试四个边界。功能已跑通，但历史兼容和阶段性验收把一部分测试设施留在生产源码，把测试实现细节写进配置/使用文档，并形成若干重复断言。清理必须在不降低 owner 隔离、敏感数据、幂等、事务和外部系统回归强度的前提下进行。

## Goals / Non-Goals

**Goals:**

- 生产 wheel 不再公开或包含只供测试构造候选的 backend。
- Agent 普通连接配置只保留当前两个正式变量名。
- 每项自动化测试对应一个清晰的产品、架构或外部边界，删除已有更强覆盖的重复测试。
- 读者文档各司其职：详细设计、配置、Agent 接入、操作、测试、评测、日志和部署不重复维护同一大段内容。

**Non-Goals:**

- 不以减少数量为目的删除 owner、安全、事务、PostgreSQL、MCP Transport、Hook 生命周期或模型结构化输出测试。
- 不重构大型 Repository、领域模型或 MCP 工具，不修改 PostgreSQL schema。
- 不删除 `evals/cases.json` 或缩小投研质量覆盖；benchmark 和 pytest 继续承担不同职责。
- 不改变静态 Token、Profile 或主动记忆的运行语义。

## Decisions

### 1. 测试替身只能位于测试边界

删除 `memory_mcp.extraction.FixedCandidateBackend`。PostgreSQL/MCP 确定性用例直接注入已有 `tests.support.FakeCandidateExtractor`，不再为了测试把 JSON fixture parser 和 fixed backend 发布到 Server wheel。

拒绝把 Fixed Backend 移到另一个生产模块，因为它没有运行时消费者；也拒绝给生产配置增加 backend 开关。

### 2. 只删除有明确替代覆盖的测试

删除目标限于：旧配置别名、已经由同层设置测试覆盖的缺失凭据、生产对象删除后失效的 fixed backend、评测器内部缺预测自测、空 `__init__` 的 eager-import 实现细节、以及单字段空日志转换。三个关系拒绝测试合并为一个表驱动回归，仍执行否定、证据不足和反向三种输入。

保留所有跨 owner、敏感内容、幂等/并发、relation 生命周期、数据库 migration/checksum/transaction、真实 HTTP/MCP、Agent 状态和 fail-open/closed 用例。测试总数下降是去重结果，不是验收目标。

### 3. 正式 Agent 连接名不再兼容旧别名

`MemoryHookSettings` 的连接字段只读取 `MEMORY_MCP_URL` 和 `MEMORY_MCP_TOKEN`。`MEMORY_HOOK_PROFILE_ID` 等高级集成默认值仍由现有 `MEMORY_HOOK_` 前缀管理，但旧 URL/Token 名称不再接受。这样普通配置合同只有一套名称，避免优先级测试和文档迁移说明长期存在。

### 4. 文档以链接代替重复说明

- `docs/design.md`：保留详细架构和原因。
- `docs/config.md`：只保留运行配置，不列测试 doubles 和完整 Profile 规则。
- `docs/agents.md`：保留宿主 Hook 接入，不重复 Server 操作。
- `docs/usage.md`：保留从启动到手工端到端的操作路径，测试、队列、日志和部署只链接权威文档。
- `docs/testing.md`：保留测试层级、命令、测试库安全、套件地图和发布验收，不复制全部断言或历史运行日志。
- `docs/evaluation.md`：继续作为评测结果唯一来源。
- `docs/README.md`：只做导航；OpenSpec 细节只链接 `openspec/README.md`。

删除 `evals/results/README.md`，因为其内容已由 `evals/README.md` 与 `docs/evaluation.md` 覆盖。

## Risks / Trade-offs

- **[旧 Agent 环境仍使用旧连接变量]** → 这是显式 breaking cleanup；部署前改为 `MEMORY_MCP_URL/TOKEN`，回滚时恢复上一版 Agent wheel。
- **[删除测试造成覆盖缺口]** → 每个删除项先确认同层替代覆盖，完成后运行全量 pytest、真实构建和质量评测。
- **[文档压缩丢失操作入口]** → 使用文档保留可执行主流程，细节链接配置/Agent/部署/评测权威文档，并执行引用扫描。
- **[测试 Fake 与生产合同漂移]** → Fake 继续实现生产 Port，并由 Core、Transport 和 PostgreSQL 合同测试共同消费；模型 schema 仍由 extraction 测试验证。

## Migration Plan

1. 先修改 Agent 配置与测试替身引用，再删除生产 fixed backend。
2. 删除/合并已确认的低价值测试，运行聚焦测试。
3. 精简文档并扫描旧变量、Fixed Backend 和失效锚点。
4. 运行全量 pytest、Ruff、离线评测、双包构建、包内容和 OpenSpec 严格校验。
5. 不执行数据库 migration。Agent 回滚使用上一版 wheel；Server 无数据回滚步骤。

## Open Questions

本轮没有阻塞项。更大规模的测试矩阵压缩应基于覆盖率和故障历史单独决策，不继续在本变更中追求数量下降。

## Acceptance Evidence

- pytest collection 从 161 项收敛到 151 项；完整结果为 142 passed、9 skipped。9 项均为未显式提供专用测试库时安全跳过的 PostgreSQL 外部测试。
- README、读者文档、评测结果目录说明和 OpenSpec 导航合计从 4507 行收敛到 3628 行，减少 879 行；删除 1 个重复的目录说明文件。
- Ruff format/check、离线投研评测和 `git diff --check` 通过；离线 Recall@K 与 safety pass rate 均为 1.0。
- Server/Agent 的 sdist 与 wheel 均成功构建；包内容检查确认没有 `tests/`、`evals/`、`FixedCandidateBackend` 或跨发行包源码泄漏。
- 仓库内 9 个当前 OpenSpec 变更全部通过 strict validation。
