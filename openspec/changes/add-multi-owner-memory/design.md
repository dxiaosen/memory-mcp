## Context

当前 owner 模型是扁平的：`owner_id = tenant_id:subject_id`，召回用 `i.owner_id = %s` 精确匹配单值。企业公共服务场景需要多层：张三的个人记忆 + 他所在投研部门的团队公共记忆，召回时合并。当前 schema 有一个死字段 `last_verified_at`（全库只赋 `=None`），需清理。

约束：owner 仍只能来自服务端认证上下文，工具参数不接受 owner；agent 只配置 URL/Token；PostgreSQL 是唯一权威；召回打分不依赖模型；core.domain 不依赖 MCP/HTTP/DB/Agent SDK；离线评测确定性可复现。

## Goals / Non-Goals

**Goals:**

- Token 配置携带团队归属，服务端派生可见 owner 集合（个人 + 团队）。
- 召回同时匹配个人和团队记忆，按相关性统一排序。
- review 确认时可显式提升候选为团队公共记忆。
- 删除 `last_verified_at` 死字段。
- 清理文档残留的失效描述。

**Non-Goals:**

- 不做自动共性检测（需共性提取/去重规则，留作后续）。
- 不改变 capture 热路径（capture 仍写个人 owner）。
- 不引入外部成员管理服务或团队目录表。
- 不改变 MCP 工具签名（`promote_to_team` 作为新增可选参数）。
- 不做团队级权限细分（团队记忆对该团队所有成员可读，提升由 review 权限控制）。

## Decisions

### 1. Token 配置携带团队归属，不新增成员映射表

`ConfiguredPrincipal` 新增 `team_ids: frozenset[str]`（可选，默认空）。团队 owner key 由 `derive_team_owner_key(tenant_id, team_id) = f"{tenant_id}:team:{team_id}"` 派生。`PrincipalContext` 扩展 `team_owner_ids: tuple[str, ...]`，由 `RequestPrincipal.to_core()` 从认证 claims 的 `team_ids` 派生。

个人 owner 仍为 `tenant_id:subject_id`，团队 owner 为 `tenant_id:team:team_id`。两者格式互不冲突（个人 subject_id 不含 `team:` 前缀，`IDENTITY_COMPONENT_PATTERN` 约束）。

`PrincipalContext` 新增 property `visible_owner_ids` 返回 `(owner_id, *team_owner_ids)`，召回和关系查询用它作为 owner 过滤集合。

**Rejected alternatives:**
- 新增 `memory_team_members` 表：支持动态调岗，但增加查询和表结构；当前静态 Token 配置已足够，且团队变动频率低。
- owner_id 用复合列（subject + team 分列）：破坏现有 owner_id 单列设计，改动过大。

### 2. 召回合并个人 + 团队候选

召回候选查询从 `i.owner_id = %s` 改为 `i.owner_id = ANY(%s)`，参数为 `list(principal.visible_owner_ids)`。关系查询 `list_relations` 同理。PostgreSQL `recall.py` 和 in_memory adapter 都改。

召回排序逻辑不变：个人和团队记忆按统一的相关性分数排序，不做来源加权（v1）。团队记忆可能因 `observed_at` 更新更频繁而占优，这是可接受的（团队共性本应更可参考）。

**Rejected alternatives:**
- 个人和团队分两路查询再合并：增加往返和复杂度，单次 `ANY(%s)` 更简洁。
- 团队记忆加权降序：当前无数据支持加权值，留作评测驱动后续。

### 3. review 确认显式提升团队记忆

`confirm_pending_memory` 工具新增可选参数 `promote_to_team: str | None`。为 None 时写入个人 owner（现有行为）；为团队 ID 时，服务端校验该 Token 的 `team_ids` 包含该团队，写入对应团队 owner。

写入团队 owner 时，`MemoryItem.owner_id` = 团队 owner key，而非个人 owner。review 的 `resolved_memory_id` 仍指向该团队记忆。capture 仍写个人 owner + pending review，热路径不变。

不做自动共性检测：是否提升为团队记忆由调用方（人或高级 Agent）在 review 时显式决定。

**Rejected alternatives:**
- capture 时自动判定团队共性：需共性提取规则（同团队多成员写同内容），改动 capture 热路径，当前无数据支持。
- 独立 `promote_memory` 工具：复用 review 确认流程更自然，不新增工具。

### 4. 删除 `last_verified_at` 死字段

`memory_revisions` 和 `memory_review_items` 两表删除 `last_verified_at` 列。`MemoryRevision`、`Candidate`、schemas（`RecalledMemory`、`MemoryDetail` 等）和召回渲染（`_render_item`）全删该字段。

该字段当前全库赋值只有 `=None`，从未被任何业务路径设为非 None。删除后渲染不再输出 `last_verified_at=None`，减少下游 Agent 的噪声。后续实现核验流程时再加回。

### 5. 文档清理

清理 `docs/design.md` 里残留的"外键""9 个 migration""last_verified_at 渲染"等失效描述，与当前 schema（单 migration、无外键、无 last_verified_at）对齐。

## Dependency Direction

- `core.domain.PrincipalContext` 扩展 `team_owner_ids`，不依赖 settings 或 auth。
- `server.settings.ConfiguredPrincipal` 加 `team_ids`，`server.auth` 派生团队 owner 注入 PrincipalContext。
- `core.application.recall_service` 用 `principal.visible_owner_ids`，不依赖具体 DB 实现。
- PostgreSQL 和 in_memory adapter 实现集合过滤。
- review 工具层加 `promote_to_team` 参数，core 的 review service 透传。

## Risks / Trade-offs

- [Token 配置团队归属不动态] → 静态配置足够当前规模；动态团队管理留作后续。
- [团队记忆对所有团队成员可读，无细粒度权限] → v1 接受；团队记忆是公共知识，提升由 review 权限控制。
- [删除 last_verified_at 后有核验需求要加回] → migration 可前向加列；当前死字段留着是噪声。
- [召回合并后团队记忆可能挤占个人记忆配额] → 候选上限 500 足够大；后续按评测数据决定是否给个人/团队分配额。

## Migration Plan

1. 新增 migration `0002_drop_last_verified_at.sql`：删除两表的 `last_verified_at` 列。
2. 开发库重建 schema（drop + 跑全部 migration）。
3. 发布 Server 代码；Agent 不受影响。
4. 跑离线评测确认 `recall_at_k` 不回退；新增多层召回 case。
5. 回滚：last_verified_at 列删除后需前向 migration 加回；多层召回代码回滚后退化单 owner。
