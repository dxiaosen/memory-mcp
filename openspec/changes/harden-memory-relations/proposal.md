## Why

自动关系已经进入 AfterRun 主链路，但当前关系只保存端点、类型和状态：它既不能说明模型依据哪一轮、哪段原文和哪个版本建边，也会在端点 revision 被替代后继续沿用旧语义。现在修正成本最低；若先积累真实关系数据，后续将难以区分“当前仍成立”和“只对旧内容成立”的边。

同时，现有测试主要验证程序合同，尚缺一套可重复运行、能量化候选抽取、关系精确率和召回命中率的离线评估基线。

## What Changes

- 为关系增加 `manual/automatic` 来源、`item/revision` 作用域，以及创建时两端 revision 快照。
- 为自动关系保存 conversation/turn、精确来源表达、confidence、expression basis 和模型/prompt/schema 版本；人工关系继续使用 item 作用域且不伪造模型证据。
- 增加 `stale` 关系状态。revision-scoped 关系任一端产生 replacement revision 时，在同一事务失效并停止参与普通详情和召回；历史仍可审计。
- 使用单个 `0007` 扩展已部署 `0006`，在约束中显式处理 PostgreSQL `NULL` 检查语义；不修改 `0006` 及更早 migration checksum。
- 扩展 MCP 关系 DTO，让 owner 能看到关系作用域、revision 快照、来源类别和自动证据元数据；默认日志继续不输出关系正文、owner 或 Secret。
- 增加确定性评估数据集和 runner，输出候选/关系精确率、召回命中率与安全边界结果；真实模型运行显式启用，普通测试不访问网络。
- 整理当前规范导航和变更状态说明，但不把公网部署、现场脚本或录屏标记为已验收。

## Capabilities

### New Capabilities

- `relation-provenance`: 关系来源证据、revision 作用域、stale 生命周期和向后兼容读取合同。
- `memory-quality-evaluation`: 可重复运行的候选、关系和召回质量评估合同。

### Modified Capabilities

无。关系底座与自动关系仍位于尚未归档的活动变更中，本变更以独立增量收紧其合同。

## Impact

- Core：关系领域模型、自动关系规划、replacement 生命周期和 Repository port 合同。
- PostgreSQL/InMemory adapter：`0007` 向前 migration、映射、活动关系过滤和事务内 stale 更新。
- MCP：关系详情 DTO 增加只读字段，不改变工具名称、认证方式和 Agent 配置。
- 测试/评估：新增离线评估数据和 runner；现有单元、契约、端到端测试同步更新。
- 部署：Server 需要先执行 migration 后重启；Agent 包、Hook、URL 和 Token 不变。
- 非目标：完整知识图谱、多跳推理、自动判断任意自然语言关系已失效、关系待确认 UI、队列化 Capture、向量数据库和生产来源核验器。
