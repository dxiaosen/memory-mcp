# 阶段三设计：远程 Memory MCP 与可信身份边界

版本：2026-07-30  
对应 OpenSpec：`add-general-memory-core` 3.1～3.8  
实现状态：已完成

> 后续架构决策：本文记录阶段三已经验收的 SQLite 原型实现，不代表最终部署
> 存储。项目现已确定采用平台无关的公网 MCP、Linux ECS 与托管 PostgreSQL；
> PostgreSQL 迁移和部署属于阶段四、五，详见
> [当前架构](../architecture.md)和
> [ECS 部署说明](../deployment/aliyun-ecs.md)。百炼等平台仅为可选客户端。

## 1. 阶段目标

阶段三不是重新实现 Memory Core，而是在阶段一、二已经完成的领域能力之外增加
一个唯一正式的远程产品入口：

```text
Codex / Agent A / Agent B / MCP Inspector
                    │
                    │ MCP Streamable HTTP + Bearer Token
                    ▼
             Memory MCP Server
                    │
                    │ trusted PrincipalContext
                    ▼
        Memory Application / Domain / Policy
                    │
                    ▼
              SQLite Repository
```

阶段三需要证明：

1. 不同进程、不同 Agent 可以连接同一个远程服务；
2. owner 只能由服务端认证上下文产生，不能由工具参数或模型决定；
3. 同一用户的不同 Agent 共享记忆，不同用户保持隔离；
4. 阶段二的四类准入、敏感拦截、pending 和幂等捕获可通过 MCP 调用；
5. 服务可由官方 MCP Client 和 MCP Inspector 验证；
6. MCP 入口接管产品形态后，旧 RAG 产品线可以被移除。

阶段三不包含主动召回、duplicate/replacement、Hook SDK 和真实模型默认 backend。
这些分别属于阶段四、五、六。

## 2. Review 后的设计调整

阶段三实现完成后的整体 review 发现并修正了四个边界问题。

### 2.1 消息角色不能只停留在外部 DTO

原实现接收 `user/assistant/tool`，但进入 Core 前会拼成一段文本。候选的
`source_expression` 只需出现在整段文本中，程序无法证明自动保存的表达来自用户
而不是助手。

调整后：

- `TurnEnvelope` 保留经过校验的 `TurnMessage[]`；
- Candidate 和 Evidence 保留 `source_role`、`source_message_id` 和
  `source_tool_name`；
- 来源字段由服务端根据 source expression 和消息块推导，不信任模型自报；
- 来自 assistant/tool 的候选即使满足高置信、持久、显式等条件，也最多进入
  pending，不能自动保存成用户观点；
- 旧的阶段二进程内调用没有消息块时保持原有兼容行为。

### 2.2 token 配置需要防止 owner 错误别名

只校验 token 是否存在并不足够。如果配置人员误把两个不同
`tenant_id + subject_id` 映射到同一 `owner_key`，会造成逻辑越权。

现在启动时验证双向一致性：

```text
一个 tenant/subject 只能映射一个 owner_key
一个 owner_key 不能代表两个不同 tenant/subject
```

同一用户可以配置多个 token/client，它们必须使用相同 owner。

### 2.3 actor 审计需要区分 client 与 Agent

审计日志原来记录 owner 和 client。现在当可信配置包含 `agent_id` 时，额外记录
`agent_ref`。三个值都只记录不可逆稳定引用，不记录原始身份或 token。

### 2.4 健康检查必须安全降级

`/health` 现在统一处理数据库不存在、SQLite 错误和文件系统错误：

- 正常：HTTP 200，返回 `status=ok`；
- 异常：HTTP 503，只返回 `status=unhealthy`；
- 不向客户端暴露数据库路径、SQL 或异常堆栈。

## 3. 模块和依赖边界

### 3.1 目录

```text
src/agent_lab/
├── memory/
│   ├── domain/                  # 通用领域对象
│   ├── application/             # 捕获、准入、审核用例
│   ├── ports/                   # Repository/Extractor/Policy 契约
│   ├── adapters/
│   │   ├── sqlite/              # 权威原型存储
│   │   ├── in_memory.py         # 离线测试替身
│   │   ├── sensitive.py         # 敏感检测
│   │   └── structured_model.py  # 结构化模型结果解析
│   └── composition.py
├── memory_mcp/
│   ├── server.py                # MCP/ASGI composition root
│   ├── settings.py              # 服务配置和 token principal 映射
│   ├── auth.py                  # 认证上下文到 RequestPrincipal
│   ├── schemas.py               # 外部事件和结构化响应
│   ├── tools.py                 # 六个 MCP 工具
│   ├── policy.py                # 阶段三可配置场景
│   └── errors.py                # 稳定边界错误
└── integrations/
    └── chat_models.py           # 阶段六真实 extractor 可复用
```

### 3.2 依赖方向

```text
MCP SDK / HTTP
      │
      ▼
memory_mcp
      │
      ▼
memory.application
      │
      ├── memory.domain
      └── memory.ports
               ▲
               │
       SQLite / model adapters
```

约束：

- Domain、Application、Ports 不依赖 MCP SDK、HTTP、Agent Runtime 或模型 SDK；
- MCP 工具不直接执行 SQL；
- MCP Client 不直接访问 SQLite；
- ScenarioPolicy 提供词表与规则，Core 不硬编码正式业务场景；
- 旧 RAG 的 Agent、Knowledge、Chroma、Embedding 和 CLI 不再属于产品边界。

## 4. 服务组合和启动

`create_memory_mcp_server()` 的组合顺序：

```text
读取 MemoryServerSettings
  → 解析并验证 demo token mapping
  → 创建/迁移 SQLite
  → 注册 ConfiguredScenarioPolicy
  → 组装 MemoryService
  → 创建带 TokenVerifier 的 FastMCP
  → 注册六个工具
  → 收紧工具 additionalProperties=false
  → 注册 /health
```

正式入口：

```powershell
uv run memory-mcp
```

等价入口：

```powershell
uv run python -m agent_lab
uv run python -m agent_lab.memory_mcp
```

默认地址：

```text
MCP     http://127.0.0.1:8765/mcp
Health  http://127.0.0.1:8765/health
```

默认只监听 `127.0.0.1`。如果现场需要局域网访问，必须显式修改 host，并继续使用
认证；“远程服务”不意味着默认暴露到所有网卡。

## 5. 可信身份模型

### 5.1 RequestPrincipal

```text
RequestPrincipal
├── owner_key      Memory Core 的隔离键
├── tenant_id      租户/命名空间
├── subject_id     最终用户或服务主体
├── client_id      MCP Client / Agent 应用
├── agent_id?      可选 Agent 实例
└── scopes         read / write / review
```

外部工具参数不出现以上身份字段。服务端从已经验证的 access token 创建
`RequestPrincipal`，随后只向 Core 传递：

```python
PrincipalContext(owner_id=principal.owner_key)
```

### 5.2 原型 token 映射

环境变量保存 JSON 映射：

```json
{
  "<random-token>": {
    "owner_key": "demo-user",
    "tenant_id": "demo",
    "subject_id": "demo-user",
    "client_id": "agent-a",
    "agent_id": "agent-a",
    "scopes": ["memory:read", "memory:write", "memory:review"]
  }
}
```

安全规则：

- 映射为空时拒绝启动；
- token 只从配置加载，不在服务端签发；
- token 不进入日志、响应或数据库；
- owner 与 tenant/subject 映射必须双向一致；
- 同一 owner 可以通过不同 client/agent token 接入；
- 该方案只用于研究原型，不等同于生产 OAuth、SSO 或合规认证。

### 5.3 Scope

| Scope | 工具 |
| --- | --- |
| `memory:read` | `list_memories`、`get_memory` |
| `memory:write` | `capture_completed_turn` |
| `memory:review` | `list_pending_reviews`、`confirm_pending_memory`、`reject_pending_memory` |

权限检查在调用 Application Service 前执行。缺少权限返回
`permission_denied`，不进行数据库读写。

### 5.4 隔离矩阵

| Token | owner | client | 预期 |
| --- | --- | --- | --- |
| 用户 A / Agent A | A | agent-a | 可写入 A |
| 用户 A / Agent B | A | agent-b | 可读取和审核 A |
| 用户 B / Agent B | B | agent-b | 看不到 A |
| 用户 A / read-only | A | read-client | 可读取，不可捕获 |

跨用户猜测 memory/review UUID 与对象不存在返回相同 unavailable 结果，不暴露
目标是否存在、属于谁或处于什么状态。

## 6. MCP Transport

阶段三使用 Streamable HTTP：

```text
POST/GET /mcp
Authorization: Bearer <token>
Accept: application/json, text/event-stream
```

选择原因：

- 多个 Agent 进程可连接同一服务；
- 可在 transport 层认证；
- 服务状态独立于 Agent 生命周期；
- 便于 Inspector、健康检查和后续部署。

无效或缺失 token 在进入工具之前由认证 middleware 拒绝，真实 HTTP 验收结果为
401。业务权限、幂等、场景和对象不可用则使用结构化 `ErrorResponse`。

## 7. 外部事件契约

### 7.1 CompletedTurnEventV1

```text
CompletedTurnEventV1
├── contract_version
├── event_id
├── scenario
├── conversation_id
├── turn_id
├── observed_at
├── subject_hint?
└── messages[1..64]
    ├── role: user | assistant | tool
    ├── content
    ├── message_id?
    └── tool_name?
```

字段用途：

| 字段 | 当前用途 | 后续用途 |
| --- | --- | --- |
| `contract_version` | 显式只接受 `"1"` | Hook/Server 独立升级 |
| `event_id` | 跨重试幂等键 | 消息队列/离线重放 |
| `scenario` | 选择策略和存储范围 | 多正式场景 |
| `conversation_id` | 来源追溯 | 会话级审计 |
| `turn_id` | 来源追溯和旧调用兼容 | run/turn 定位 |
| `observed_at` | 可信观察时间 | 时序冲突和有效期 |
| `subject_hint` | 已校验但阶段三不参与准入 | 阶段四对象匹配 |
| `message_id` | fingerprint 和来源追溯 | 多消息 Evidence |
| `tool_name` | 工具来源追溯 | 外部事实策略 |

事件不接受 `owner_id`、`owner_key` 或 `tenant_id`。任何额外字段均由严格 schema
拒绝。

### 7.2 payload fingerprint

服务端对 DTO 的 canonical JSON 计算 SHA-256：

```text
UTF-8
sort_keys=true
compact separators
完整包含角色、消息 ID、正文、时间和场景
```

fingerprint 不作为认证或签名，只用于判断相同 event 是否携带完全一致的 payload。

### 7.3 角色来源

处理规则：

| 来源 | 可以自动保存 | 默认行为 |
| --- | --- | --- |
| user | 条件满足时可以 | 进入正常四类准入 |
| assistant | 不可以 | 最多 pending |
| tool | 不可以 | 最多 pending |
| 找不到对应消息块 | 不可以 | 视为非法模型输出，整次 capture 失败 |

模型只能建议 `source_expression`。服务端在脱敏后的消息块中定位该表达并派生
source role/message/tool；模型无法伪造这些字段。

## 8. 六个 MCP 工具

### 8.1 capture_completed_turn

用途：AfterRun Hook 提交成功完成的一轮。

输入：

```text
event_id
contract_version
scenario
conversation_id
turn_id
observed_at
messages[]
subject_hint?
```

成功响应：

```text
ok
request_id
capture_id
status
replayed
policy_version
summary
  auto_saved_count
  pending_count
  discarded_count
  blocked_count
created_memory_ids[]
pending_review_ids[]
failure_code?
```

工具注解：write、non-destructive、idempotent、closed-world。

默认 CLI 组合根尚未注入真实 `CandidateExtractor`，因此未配置时返回
`capture_not_configured`。测试和可注入组合根已经验证完整捕获链路；阶段六把真实
和固定 backend 接到默认入口。

### 8.2 list_memories

输入：

```text
scenario?
subject?
memory_type?
limit=50
cursor?
```

只返回当前 owner 的 active/current 摘要，不返回 Evidence/source expression；
需要来源时再调用 `get_memory`。阶段三数据规模很小，Application
先按 owner 从 Repository 读取，再由 MCP facade 过滤和分页；阶段四增加 recall
query 后应把过滤和分页下推到 Repository port。

### 8.3 get_memory

输入：

```text
memory_id
include_history=false
```

阶段三只返回当前 revision 和 Evidence。`include_history` 已保留在契约中，但在
阶段四建立 superseded history 前，响应明确返回：

```text
history_included=false
history=[]
```

不会伪造历史，也不会把参数静默解释成其他行为。

### 8.4 list_pending_reviews

列出当前 owner 尚未处理的候选，包含：

- proposed content；
- memory type、subject、assertion kind；
- reason category；
- source expression；
- source role/message/tool；
- observed/created time。

pending 与 active memory 分表保存，确认前不会出现在活动记忆列表或未来召回中。

### 8.5 confirm_pending_memory

输入 `review_id`，在一个 Repository 事务中：

```text
校验 owner
  → 校验仍为 pending
  → 创建 MemoryItem/Revision/Evidence
  → review = confirmed
  → 写入 resolved_memory_id
```

重复确认返回第一次生成的同一 memory，不创建第二条。

### 8.6 reject_pending_memory

输入 `review_id`，把 owned pending item 变为 rejected，不创建 memory。重复拒绝
返回稳定 rejected 状态。

## 9. 捕获处理流水线

```text
MCP request
  → Transport 验证 Bearer Token
  → Scope 检查
  → 严格 DTO 校验
  → contract_version 检查
  → canonical fingerprint
  → 加载 ScenarioPolicy
  → 查询 event 幂等状态
      ├── 同 payload + 已完成 → replay
      └── 不同 payload → idempotency_conflict
  → 整轮敏感预检与脱敏
  → CandidateExtractor
  → 结构化模型输出校验
  → source expression/role 服务端校验
  → memory type/progress 策略校验
  → 持久化前敏感二次检查
  → 确定性四类准入
  → 单事务写 capture/memory/review/outcome
  → 返回无 blocked/discard 正文的 receipt
```

### 9.1 四类准入

| 结果 | 条件 | 是否保存正文 | 是否可用于召回 |
| --- | --- | --- | --- |
| `auto_save` | user 来源、显式、durable、高置信、合法类型、非敏感 | active memory | 是 |
| `pending` | 推断、含糊、低置信、不确定持久性、assistant/tool 来源 | review item | 否 |
| `discard` | 仅当前轮有效的临时要求 | 否 | 否 |
| `blocked` | 输入或模型输出命中禁止内容 | 否，只保存类别/数量 | 否 |

自动保存不是“模型认为值得保存就保存”。模型负责提出候选，程序负责身份、来源、
场景、敏感和准入边界。

### 9.2 用户确认交互

```text
Agent 提交完成轮次
  → capture receipt 返回 pending_review_ids
  → 客户端调用 list_pending_reviews
  → 向用户展示候选和来源
  ├── 用户确认 → confirm_pending_memory → active memory
  └── 用户拒绝 → reject_pending_memory → rejected
```

阶段五 Hook Client 可以把这个流程包装成 Host 交互，但治理决定仍由 MCP Server
完成。

## 10. 幂等模型

MCP 事件使用：

```text
(owner_id, scenario, event_id, policy_version)
```

旧阶段二进程内事件继续兼容：

```text
(owner_id, scenario, conversation_id, source_turn_id, policy_version)
```

状态：

```text
首次处理
  ├── completed
  ├── failed                 非法模型输出，不自动重试
  └── reprocess_required     临时中断，可使用同 event 重试

再次处理
  ├── fingerprint 不同       idempotency_conflict
  ├── completed/failed       返回原结果，replayed=true
  └── reprocess_required     保留 capture_id 后重新处理
```

capture、memories、reviews 和 outcomes 在同一 SQLite 事务提交，避免只写入其中一
部分。

## 11. SQLite 持久化

### 11.1 迁移

| 迁移 | 内容 |
| --- | --- |
| `0001_memory_core.sql` | scenario、item、revision、evidence |
| `0002_memory_capture.sql` | capture run、review、outcome、时间字段 |
| `0003_mcp_events.sql` | event/version/fingerprint、review resolution |
| `0004_message_provenance.sql` | Evidence/review 的角色、消息和工具来源 |

### 11.2 主要表

```text
memory_scenarios
  └── memory_scenario_types

memory_items
  └── memory_revisions
        └── memory_evidence

memory_capture_runs
  ├── memory_capture_outcomes
  └── memory_review_items
```

关键约束：

- item/revision/evidence/review 全部携带 owner；
- registered scenario/type 外键；
- 每个 logical memory 只能有一个 current revision；
- event 幂等唯一索引；
- review 的 pending/confirmed/rejected 状态形状；
- blocked/discard outcome 不允许引用 memory/review；
- confirmed review 记录唯一 `resolved_memory_id`。

`0004` 字段允许 NULL，用于兼容阶段一、二已有数据；所有新 MCP 捕获会填写派生的
消息来源。

### 11.3 进程模型

阶段三支持：

- 单个 MCP Server 进程；
- 一个 SQLite 文件；
- 多个远程 HTTP Client；
- 短事务、`foreign_keys=ON`、`busy_timeout=5000`。

阶段三不声称支持多个 Server worker 并发写入同一 SQLite。出现多 worker、正式
备份恢复或高并发需求时，通过 Repository port 迁移 PostgreSQL。

## 12. 错误模型

结构化业务错误：

| error_code | 含义 | retryable |
| --- | --- | --- |
| `unauthenticated` | 无可信身份 | 否 |
| `permission_denied` | 缺少 scope | 否 |
| `scenario_not_registered` | 未注册场景 | 否 |
| `invalid_event` | 事件/UUID/cursor 不合法 | 否 |
| `unsupported_contract_version` | 不支持的事件版本 | 否 |
| `idempotency_conflict` | event 对应不同 payload | 否 |
| `memory_unavailable` | 不存在或不属于当前 owner | 否 |
| `review_unavailable` | 不存在或不属于当前 owner | 否 |
| `capture_not_configured` | 没有 extractor | 否 |
| `temporarily_unavailable` | 未知临时服务异常 | 是 |

响应不包含 SQL、数据库路径、Python 异常、堆栈、token 或其他 owner 信息。

额外工具参数由 MCP schema 层拒绝，返回 protocol/tool validation error，而不是
业务 `invalid_event`。这是防 impersonation 的第一层；Application 和 Repository
仍会再次按 owner 校验。

## 13. MCP Schema 兼容策略

项目锁定 `mcp==1.29.0`。

该版本 FastMCP 生成的函数参数模型默认会忽略额外字段。为了确保
`owner_id` 等字段不是“看似拒绝、实际忽略”，注册工具后会把生成的 Pydantic
模型设置成 `extra=forbid` 并重新生成 schema。

这一步目前使用 SDK 的内部 tool manager，因此：

- 依赖必须精确锁版本；
- 每次升级 SDK 必须运行 schema 和真实 transport 测试；
- `tools/list` 必须验证 `additionalProperties=false`；
- owner 注入必须验证 `isError=true`；
- 如果未来 SDK 提供公开 strict-argument 配置，应替换当前兼容层。

## 14. 安全日志

允许记录：

- request id；
- tool name；
- status/error code；
- duration；
- result count；
- capture/event 的稳定引用；
- owner/client/agent 的不可逆稳定引用；
- scenario/policy version 等非正文元数据。

禁止记录：

- Bearer Token；
- 用户、助手或工具正文；
- memory content；
- pending proposed content；
- source expression；
- 被拦截的敏感原文；
- 模型 API Key。

日志字段名还经过集中敏感字段过滤，作为调用点之外的第二层保护。

## 15. 配置

| 环境变量 | 默认值 | 当前状态 |
| --- | --- | --- |
| `MEMORY_MCP_DATABASE_PATH` | `.agent-lab/memory.db` | 已使用 |
| `MEMORY_MCP_HOST` | `127.0.0.1` | 已使用 |
| `MEMORY_MCP_PORT` | `8765` | 已使用 |
| `MEMORY_MCP_MCP_PATH` | `/mcp` | 已使用 |
| `MEMORY_MCP_HEALTH_PATH` | `/health` | 已使用 |
| `MEMORY_MCP_STATELESS_HTTP` | `true` | 已使用 |
| `MEMORY_MCP_MAX_CAPTURE_CHARACTERS` | `100000` | 已使用 |
| `MEMORY_MCP_DEMO_TOKENS_JSON` | `{}` | 必填，空值拒绝启动 |
| `MEMORY_MCP_AUTH_ISSUER_URL` | demo URL | MCP auth metadata |
| `MEMORY_MCP_RESOURCE_SERVER_URL` | 空 | 正式资源地址预留 |
| `MEMORY_MCP_REQUEST_TIMEOUT_SECONDS` | `30` | 阶段六 extractor/阶段五 client 超时预留 |
| `MEMORY_MCP_SCENARIO_*` | project-work | 阶段三临时可配置策略 |
| `MEMORY_MCP_LOG_*` | 本地滚动日志 | 已使用 |

`request_timeout_seconds` 暂未进入关键路径，但保留有明确激活条件；它不应被误认为
当前已经能够中断同步模型调用。

## 16. 客户端接入

最小客户端：

```powershell
uv run python examples/memory_mcp_client.py `
  --url http://127.0.0.1:8765/mcp `
  --token <local-token> tools
```

管理命令：

```powershell
uv run python examples/memory_mcp_client.py --token <local-token> memories
uv run python examples/memory_mcp_client.py --token <local-token> pending
```

客户端应读取 `structuredContent`，不要解析自然语言文本。当前 SDK 对 union result
可能增加一层 `result` 包装，示例客户端已经兼容有无该包装的两种形状。

阶段三证明“Agent 可以连接和调用 MCP”。自动 BeforeRun/AfterRun Hook 属于阶段
五，不能把普通 MCP 连接能力描述成已经实现自动 Hook。

## 17. 测试和验收矩阵

| 类别 | 覆盖 |
| --- | --- |
| DTO | 未知字段、时间时区、角色、fingerprint |
| Schema | 六个工具、无 owner 字段、额外参数拒绝 |
| Auth | 无 token、错误 token、scope 缺失、owner alias 配置 |
| Capture | auto/pending/blocked、非用户来源降级、版本错误 |
| Sensitive | 响应和 SQLite 均无敏感测试明文 |
| Idempotency | replay、payload conflict、SQLite 重开 |
| Review | list、confirm/reject、重复操作稳定 |
| Isolation | 同 owner 跨 Agent、不同 owner UUID 猜测 |
| Transport | 真实 Uvicorn + MCP Client Session |
| Compatibility | MCP Inspector `tools/list` |
| Quality | pytest、Ruff、OpenSpec strict validation |

验收结果详见 `phase-three-acceptance.md`。

## 18. 当前限制和后续调整

### 18.1 阶段四必须完成

- `GeneralWorkPolicy`；
- duplicate Evidence 合并；
- 明确 replacement 和 superseded history；
- owner-first 的结构化 recall；
- `recall_memory`；
- `get_memory(include_history=true)` 的真实历史。

### 18.2 阶段五必须完成

- 唯一的 `MemoryMcpClient`；
- BeforeRun/AfterRun Hook Bridge；
- 稳定 event id 生成和有限重试；
- Codex/第二客户端接入；
- A/Agent A 写、A/Agent B 召回、B 空结果。

### 18.3 阶段六必须完成

- 默认真实结构化模型 backend；
- 固定离线 backend；
- 可重置演示数据库；
- 10～15 个演示脚本；
- 延迟测量；
- 从空库启动和录屏兜底。

### 18.4 已接受的阶段三权衡

| 权衡 | 当前决定 | 触发调整条件 |
| --- | --- | --- |
| 同步 Core 调用会阻塞 event loop | 单进程原型可接受 | 接真实慢模型时转线程/异步 port |
| list 后在 facade 过滤分页 | 小数据可接受 | 阶段四 recall query 下推 |
| demo token 配置 | 仅演示 | 对公网/真实用户前接 OAuth |
| SQLite 单 worker | 现场稳定优先 | 多 worker/高并发时 PostgreSQL |
| SDK 私有 strict 兼容层 | 精确 pin + 回归测试 | SDK 提供公开 strict API |
| `subject_hint` 暂未使用 | 保留但不参与准入 | 阶段四对象匹配 |
| 默认无 extractor | 明确错误，不伪造 | 阶段六接真实/固定 backend |

## 19. 现场说明口径

阶段三可以准确表述为：

> 已经实现一个可由不同 Agent 远程连接的 Memory MCP Server，具备可信身份、
> 用户隔离、捕获准入、pending 治理和幂等能力。

阶段三不能表述为：

> Codex 已经自动在每轮前后调用记忆，或者服务已经具备完整主动召回。

前者属于阶段五 Hook，后者还需要阶段四 `recall_memory`。

## 20. 结论

阶段三已经把项目从“进程内 Memory Core”推进为真正的 MCP-first 对外服务。
Memory Core 继续负责稳定领域语义，MCP 层负责 transport、可信身份、外部契约和
错误映射。角色来源、owner 映射、幂等和敏感边界均由程序规则约束，而不是依赖
Agent 或模型自觉。

下一阶段可以在不改变现有六个工具身份边界的前提下，增加 lifecycle 和
`recall_memory`，然后由 Hook SDK 把服务接入不同 Agent 的生命周期。
