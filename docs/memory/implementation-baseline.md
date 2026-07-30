# Memory MCP 阶段一至四实现基线

本文合并阶段一至四的设计与验收记录，描述当前已经实现并受测试保护的行为。
当前目标、未完成需求和实施顺序以
[`add-general-memory-core`](../../openspec/changes/add-general-memory-core/design.md)
及其 `specs/`、`tasks.md` 为事实源。

## 1. 已实现边界

阶段一至四的代码实现已经完成：

- 框架无关的 Memory Core、可信来源模型和 owner 隔离；
- PostgreSQL 版本化 migration、约束、健康检查和重新打开验证；
- 完成轮次捕获、结构化候选、四类准入和 pending 审核；
- 敏感内容预检、持久化前复检和无正文 CaptureOutcome；
- source turn 与 event 级幂等、payload conflict 和失败重处理；
- 带 Bearer Token 鉴权的 Streamable HTTP MCP 服务；
- 七个管理、捕获与召回工具、稳定错误码、严格 DTO 和无正文操作日志；
- `GeneralWorkPolicy`、duplicate Evidence、replacement revision 和显式 history；
- owner-first active/current recall、安全渲染、阈值、数量和 token budget；
- 真实 MCP Client/Inspector、同 owner 跨 Agent 共享和跨 owner 隔离验证。

PostgreSQL schema、migration、连接池、Repository 和 Linux 部署骨架已经完成。
真实 Repository contract 与 MCP 重启套件已在隔离的 RDS 测试库通过，SQLite
原型路径已经删除。本文固化阶段一至四的 Core 基线；阶段五新增的 Hook SDK、
真实/固定模型组合根和端到端流程见[整体设计](../design.md)。

## 2. 当前目录和依赖方向

```text
src/memory_mcp/
├── core/
│   ├── domain/          # 领域模型
│   ├── application/     # 创建、捕获、审核、生命周期和召回用例
│   ├── ports/           # Repository、Extractor、ScenarioPolicy
│   ├── adapters/        # PostgreSQL、InMemory、敏感守卫、结构化输出
│   └── composition.py
├── extraction/          # 模型 settings/provider/backend/factory
├── scenarios/
│   └── general_work.py  # 正式通用工作场景
├── server/
│   ├── app.py           # MCP/ASGI 组合根
│   ├── auth.py          # token -> RequestPrincipal
│   ├── settings.py
│   ├── schemas.py
│   ├── tools/           # capture / memory / recall / review
│   └── errors.py
├── memory_hooks/        # 远程 Client、Bridge 和 Runner
├── database_cli.py      # 部署层数据库维护入口
└── logging.py           # 统一结构化日志
```

依赖方向：

```text
server ──────────────> core.application ─────> core.domain / core.ports
  │                            ▲                         ▲
  └── auth / DTO               └──── infrastructure ────┘
```

约束：

- `core.domain`、`application` 和 `ports` 不依赖 MCP、HTTP、Agent SDK、
  PostgreSQL 驱动或运行配置；
- `server` 不直接执行 SQL；
- Repository 的所有用户数据操作显式接收可信 `PrincipalContext`；
- 场景差异只通过 `ScenarioPolicy` 进入 Core；
- Agent Client 不直接访问 Repository 或数据库。

依赖守卫位于 `tests/core/test_dependency_boundaries.py`。

## 3. 身份与隔离

远程请求使用两个不同概念：

```text
tenant_id + subject_id + owner_key  → 私有记忆范围
client_id + agent_id                → 调用方与审计 actor
scopes                              → read / write / review
```

原型 Token 映射由 `MEMORY_MCP_DEMO_TOKENS_JSON` 提供。`owner_key` 是映射中的
可信、不透明隔离键，服务端校验 tenant/subject 与 owner 之间的一一关系；它不是
由工具参数提供，也不等同于生产 OAuth。

MCP DTO 不接受 `owner_id`、`tenant_id` 或 impersonation 字段。认证适配器把
`owner_key` 转换为 Core 使用的 `PrincipalContext.owner_id`。跨 owner 的 memory
或 review identifier 与不存在使用相同 unavailable 语义。

权限分为：

- `memory:read`：列表、详情和后续召回；
- `memory:write`：提交完成轮次；
- `memory:review`：查看、确认和拒绝 pending。

## 4. 领域模型

一条当前记忆由以下对象组成：

```text
MemoryItem
  └── current MemoryRevision
        └── one or more Evidence
```

- `MemoryItem` 保存稳定逻辑身份、owner、scenario、subject 和 memory type；
- `MemoryRevision` 保存内容、assertion kind、生命周期、形成理由和时间；
- `Evidence` 保存 conversation、source turn、原始允许表达和消息角色；
- `MemoryRecord` 保证 Item、current revision 和 Evidence 的 owner/id 一致。

生命周期枚举为 `active / superseded / expired / revoked`。新记忆创建初始
revision；明确 replacement 在同一 MemoryItem 内追加 revision，并把旧 revision
原子变为 non-current superseded。规范化等价的 duplicate 不创建新 revision，
只向 current revision 增加 Evidence。

`AssertionKind` 区分：

- `user_view`
- `user_provided_fact`
- `external_fact`
- `system_inference`

这些标签不能被渲染为同一种“已验证事实”。

## 5. 场景扩展

`ScenarioPolicy` 声明：

- scenario id 与合法 memory types；
- 可选 business progress；
- capture guidance 和 policy version；
- relation 和 recall priority。

Core 不包含正式场景词义。默认组合根注册独立的 `GeneralWorkPolicy`：
`general-work` 场景允许 `preference`、`stable_context`、`ongoing_item` 和
`decision`，关系集合保持为空，并提供最小类型召回优先级。

## 6. 捕获流水线

`capture_completed_turn` 接收 `contract_version=1`、稳定 `event_id`、scenario、
conversation、turn、带时区 `observed_at` 和带角色 messages，不接收 owner。

处理顺序：

```text
可信 principal + 完成轮次
  → event/payload 幂等检查
  → 原始轮次敏感检测与脱敏
  → CandidateExtractor 结构化抽取
  → 所有可持久化候选字段二次敏感检查
  → 来源表达和消息角色校验
  → ScenarioPolicy 类型/进展校验
  → 确定性准入
  → new / duplicate / replacement / pending 分类
  → CaptureResult + Memory/Review/lifecycle write 原子提交
```

四类互斥准入结果：

| 结果 | 行为 |
| --- | --- |
| `auto_save` | 创建 active memory、追加 duplicate Evidence 或明确 replacement |
| `pending` | 创建隔离的 ReviewItem，确认前不可普通读取 |
| `discard` | 只保存无正文 outcome |
| `blocked` | 只保存非正文类别和 outcome |

只有明确、持久、高置信且来自 user message 的表达可以自动保存。assistant/tool
来源即使抽取器建议 auto-save，也会降级为 pending。

相同 event/payload 重试返回原结果并标记 replay；相同 event 使用不同 payload
返回 `idempotency_conflict`。处理被意外中断时保存 `reprocess_required`，后续
相同事件可复用 capture id 重新处理。

## 7. Pending 审核

pending 与 active memory 分表保存。当前 owner 可以：

- 列出 pending；
- 确认并在同一事务中创建 active memory，或应用 duplicate/replacement；
- 拒绝且不创建 memory；
- 安全重试已经完成的相同决定。

跨 owner review id 不暴露内容和存在性。

## 8. MCP 边界

当前七个工具：

| 工具 | Scope |
| --- | --- |
| `capture_completed_turn` | `memory:write` |
| `list_memories` | `memory:read` |
| `get_memory` | `memory:read` |
| `recall_memory` | `memory:read` |
| `list_pending_reviews` | `memory:review` |
| `confirm_pending_memory` | `memory:review` |
| `reject_pending_memory` | `memory:review` |

服务使用 MCP Python SDK 的 Streamable HTTP transport，默认路径 `/mcp`，健康路径
`/health`。SDK 生成的工具入参模型被收紧为 `extra=forbid`，防止未声明 owner 字段
被静默忽略。同步 Core、模型和 Repository 调用由 worker thread 执行，避免阻塞
MCP 事件循环。

稳定错误包括 unauthenticated、permission denied、invalid event、unsupported
contract version、idempotency conflict、memory/review unavailable、
capture not configured 和 temporarily unavailable。

## 9. 存储

PostgreSQL 是唯一运行时存储：

- 使用 UUID、TIMESTAMPTZ、外键、部分唯一索引和事务；
- migration 保存 checksum 并使用 advisory lock；
- 健康检查验证必需表、migration 版本和 checksum；
- 连接池由 ASGI 服务 lifespan 关闭；
- lifecycle/recall 查询索引和同事务 current 切换。

真实 PostgreSQL 行为契约、并发幂等、review resolution、checksum、连接池关闭
和 MCP 重启验收均已通过。作为前三阶段行为基线的 SQLite 运行路径、adapter、
migration 与专项测试随后删除；快速单元测试使用 InMemory Repository。

## 10. 日志与敏感边界

日志只记录 request/capture/event 技术引用、owner/client/agent 的稳定假名引用、
tool、scenario、status、数量、耗时和错误码。

不得记录：

- Bearer Token、数据库 DSN 或模型 API Key；
- user/assistant/tool 正文；
- candidate、memory 或 Evidence 正文；
- backend 异常消息；
- 被敏感规则拦截的原文。

敏感正则只是研究原型的持久化边界，不代表生产 DLP、合规审计或上游系统安全。

## 11. 验收证据

阶段验收覆盖：

- 阶段一：Memory Core、owner 隔离和依赖守卫；
- 阶段二：多候选、四类准入、脱敏、pending 和失败重处理；
- 阶段三：真实 Uvicorn + 官方 MCP Client、401、严格工具 schema、七工具发现、
  同 owner 跨 Agent、跨 owner identifier 和 event replay/conflict；
- 阶段四：真实 PostgreSQL migration/Repository/MCP 重启，以及确定性规范化、
  duplicate、replacement、pending 确认、history 排除、owner-first/空召回、
  当前指令优先、token budget 和跨 owner 召回。

统一验证命令：

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
openspec-cn validate add-general-memory-core --strict
```

真实 PostgreSQL 套件要求 `MEMORY_MCP_TEST_DATABASE_URL` 指向数据库名包含
`test` 的隔离库。未配置时会 skipped；阶段四勾选依据是已记录的真实 RDS 通过
结果，而不是 skipped 的本地回归。

## 12. 阶段四收尾

阶段四已完成：

- 项目、包、测试和部署模板不再使用旧 `agent-lab` 产品命名；
- 所有可持久化候选文本均通过二次敏感检查；
- backend 异常正文不进入日志；
- lifecycle、history、recall、MCP 重启和隔离测试通过；
- 同一组 Repository contract 已在真实 PostgreSQL 验证 owner 隔离、事务、
  review resolution、event/source-turn 幂等、重叠重试、migration checksum、
  连接池关闭和 MCP 重启；
- SQLite 正式运行路径、adapter、migration 和专项测试已删除。
