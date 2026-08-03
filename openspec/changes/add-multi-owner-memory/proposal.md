## Why

当前 Memory MCP 的 owner 模型是扁平的：每个 Token 对应一个 `owner_id = tenant_id:subject_id`，召回只查这个单一 owner 的记忆。这对"一个服务给不同部门的人接入"的企业公共服务场景不够：张三和李四各有个人记忆，但他们同属一个投研部门，该部门共同关注的研究观点、风险偏好和事实结论应作为团队公共记忆逐渐沉淀，让部门成员都能召回。

与此同时，schema 中存在一个从未被写入的预留字段 `last_verified_at`（全代码库赋值只有 `=None`），属于"为未来核验流程预留"的死字段，应清理，后续真正实现核验流程时再加回。

## What Changes

- 在 `ConfiguredPrincipal` 的静态 Token 配置中新增可选 `team_ids` 字段，声明该用户所属的团队；`PrincipalContext` 扩展携带 `team_owner_ids`，由 `tenant_id` 和 `team_id` 派生团队 owner key（`tenant_id:team:team_id`）。
- 召回候选查询从 `owner_id = %s` 改为 `owner_id = ANY(%s)`，同时匹配个人 owner 和团队 owner 集合；关系查询同理。个人记忆仍写个人 owner，团队记忆写团队 owner。
- 复用现有 review 流程实现团队记忆写入：`confirm_pending_memory` 新增可选 `promote_to_team` 参数，确认时将候选写入指定团队 owner 而非个人 owner。capture 热路径不改变，不做自动共性检测。
- 删除 `last_verified_at` 字段：`memory_revisions`、`memory_review_items` 两表删列，`MemoryRevision`、`Candidate`、schemas 和渲染逻辑全删该字段。
- 清理设计、配置、测试文档中残留的失效描述（外键、9 个 migration、`last_verified_at` 渲染等）。

## Capabilities

### New Capabilities

- `multi-owner-memory`: 规定 Token 携带团队归属、PrincipalContext 携带可见 owner 集合、个人与团队记忆的存储隔离，以及召回合并个人与团队候选的合同。
- `team-memory-promotion`: 规定通过 review 确认显式提升候选为团队公共记忆的写入路径。

### Modified Capabilities

无。主规范尚未归档，本变更以独立增量能力描述新增行为。

## Impact

- Server Core：`PrincipalContext` 扩展 `team_owner_ids`，`MemoryRevision`/`Candidate` 删 `last_verified_at`。
- Server：`settings.py` 的 `ConfiguredPrincipal` 加 `team_ids`，`auth.py` 派生团队 owner，`recall_service.py` 用可见 owner 集合召回。
- PostgreSQL：一次 migration 删除两张表的 `last_verified_at` 列；召回查询改 `ANY(%s)`；不新增表。
- Agent：不改变；Agent 只发送 query，团队归属由服务端 Token 配置决定。
- 评测：离线评测继续作为召回质量基线；新增多层召回 case。
- 非目标：不做自动共性检测（需共性提取/去重规则，留作后续）；不改变 capture 热路径；不引入外部成员管理服务；不改变 MCP 工具签名（`promote_to_team` 作为新增可选参数）。
