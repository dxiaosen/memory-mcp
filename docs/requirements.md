# 主动记忆 MCP 服务需求

## 1. 项目定位

不同 Agent Runtime 通常只保存自己的会话历史，用户切换 Codex、LangChain、
自研 Agent 或其他客户端后，稳定偏好、项目背景、未完成事项和既有决策无法继续
使用。本项目建设一个独立的长期记忆 MCP 服务，让多个 Agent 在可信用户范围内
共享当前有效记忆。

正式产品边界是一个平台无关的远程 MCP Resource Server：

- 服务端通过 Streamable HTTP 暴露标准 MCP 工具；
- Agent 通过 URL 和认证信息直接接入；
- Agent Hook 或外层 Runner 在任务前召回、任务成功后捕获；
- 身份隔离、敏感拦截、准入、生命周期和持久化统一在服务端执行；
- 阿里云百炼等托管平台只是可选客户端，不是系统依赖。

本项目在 20 个工作日内交付可运行、可接入、可验证和可现场演示的研究原型，
不宣称已经建设生产级企业记忆平台。

## 2. 目标与非目标

### 2.1 目标

1. 不同 Agent Client 可以连接同一个公网 HTTPS MCP 端点。
2. 同一用户通过不同 Agent 使用同一份当前有效记忆。
3. 不同用户即使使用同一 Agent，也不能互相读取或修改记忆。
4. 一轮任务结束后主动发现具有长期价值的信息，不要求用户每次说“请记住”。
5. 新任务开始前主动提供少量相关记忆，不要求模型临时决定是否调用工具。
6. 用户可以查看、确认和拒绝未达到自动保存标准的候选。
7. 重复表达只增加来源，明确替代保留历史但不继续召回旧内容。
8. 服务可以运行在 Linux 云服务器上，并通过私网使用托管 PostgreSQL。
9. 现场能够演示跨 Agent 捕获、召回、用户隔离、幂等和敏感边界。

### 2.2 非目标

- 不实现 Agent 编排、Agent 间协商或任务调度；
- 不把某个 Agent Host 或云平台作为服务端依赖；
- 不建设 Web 管理后台；
- 不实现生产 OAuth、组织目录、企业 SSO 或数据库级 RLS；
- 不处理真实生产敏感数据；
- 不实现团队共享记忆或跨用户授权；
- 不实现 Embedding、向量数据库、HNSW 或混合检索调优；
- 不实现自动过期扫描、完整删除抑制和复杂关系图；
- 不要求 Docker、Kubernetes 或某一种反向代理。

## 3. 角色与术语

| 术语 | 含义 |
| --- | --- |
| owner | 一份私有记忆的可信用户范围 |
| subject | 认证系统中的终端用户身份 |
| tenant | subject 所属租户；原型可使用固定 demo tenant |
| client/agent | 代表用户调用 MCP 的 Agent Host，不等于 owner |
| completed turn | 已成功完成的一轮用户输入、Agent 输出和可选工具观察 |
| candidate | 从完成轮次发现的原子长期记忆候选 |
| pending | 尚未达到自动保存条件、等待用户确认的候选 |
| current memory | 当前可用于普通召回的活动记忆 revision |
| Hook | Agent 运行前或成功完成后的确定性回调 |
| Runner | Host 没有原生 Hook 时包裹 Agent 调用的外层程序 |

## 4. 核心原则

### 4.1 MCP-first

只有通过远程 MCP 可以访问的能力才属于正式产品能力。Python 内部方法用于实现和
测试，不作为对外 API。

### 4.2 owner-first

所有查询和写入必须先由认证上下文确定 owner，再执行匹配、排序或模型处理。
`owner_id`、`tenant_id` 和 impersonation 参数不能出现在 MCP 工具 schema 中。

### 4.3 当前状态优先

普通召回只返回当前、活动且已确认的记忆。pending、superseded、expired、
revoked、deleted 和 sensitive-blocked 内容不能进入 Agent 上下文。

### 4.4 来源可解释

每条记忆必须保留来源、角色、观察时间、形成原因和内容性质。用户观点、用户提供
事实、外部事实和系统推断不能被混为同一种“事实”。

### 4.5 平台无关

服务端不导入百炼、Codex、LangChain 等 Host 的专用 SDK。Host 差异由薄 Hook
adapter 或 Runner 处理。

## 5. 功能需求

### FR-01 远程 MCP 接入

- 服务必须提供单一 Streamable HTTP `/mcp` 端点；
- 端点支持标准初始化、工具发现和工具调用；
- 公网正式入口必须使用 HTTPS；
- stdio 只用于本地调试，不作为对外产品形态；
- 服务必须能被至少两个独立客户端进程访问。

### FR-02 可信身份与权限

- 每个远程工具调用都必须认证；
- 服务端把可信 credential 映射为 tenant、subject、owner、client、agent 和 scopes；
- 同一 tenant/subject 只能映射到一个 owner；
- 一个 owner 不能别名到不同 tenant/subject；
- `memory:read`、`memory:write` 和 `memory:review` 分别控制读取、捕获和审核；
- 跨用户 identifier 访问与不存在必须表现一致；
- 共享服务级 credential 如果无法区分终端用户，只能描述为单 owner 原型。

### FR-03 完成轮次捕获

`capture_completed_turn` 接收版本化事件：

- `contract_version`；
- 稳定 `event_id`；
- scenario、conversation 和 turn 标识；
- 带时区 `observed_at`；
- 有明确 role 的 user、assistant 和可选 tool message；
- 不包含 owner 选择器。

只有成功完成并得到 final output 的轮次可以由 AfterRun Hook 自动提交。

### FR-04 原子候选发现

- 一轮可以产生零个、一个或多个候选；
- 每个候选只表达一个可独立变化的长期信息；
- user 内容可以成为明确用户观点或偏好的证据；
- assistant 内容只能作为上下文，不能自动升级为用户表达；
- tool 内容默认是外部语境或系统推断；
- 临时指令、寒暄和只服务当前回答的格式要求不应保存。

### FR-05 四类准入

每个候选必须且只能得到一种结果：

| 结果 | 含义 |
| --- | --- |
| `auto_save` | 明确、持久、来源可信且符合场景策略 |
| `pending` | 有潜在价值但存在推断、歧义、相对时间或不明确冲突 |
| `discard` | 临时、重复噪声或无长期价值 |
| `blocked` | 命中禁止持久化的敏感内容 |

pending 确认前不得进入普通召回。

### FR-06 敏感持久化边界

- 模型调用前先检测和脱敏；
- 模型返回候选后、持久化前再次检测；
- 被禁止的原文不能进入记忆、Evidence、pending、语义表示、日志或 MCP 响应；
- blocked 结果只返回非正文类别和数量；
- 敏感命中不能阻止同一轮其他安全候选继续处理。

### FR-07 幂等与重试

- 相同 owner、scenario、event id、policy version 和 payload 重试返回原逻辑结果；
- replay 不能重复运行 extractor 或写入记忆、Evidence、pending；
- 相同 event id 携带不同 payload 必须返回 `idempotency_conflict`；
- 捕获事务失败不能暴露部分状态；
- PostgreSQL 重启后幂等仍然成立。

### FR-08 pending 用户控制

用户可以：

- 列出自己范围内的 pending；
- 查看候选内容、类型、来源角色和形成原因；
- 确认并原子创建活动记忆；
- 拒绝并保证以后不召回；
- 安全重试已经完成的确认或拒绝。

### FR-09 最小生命周期

本期激活：

- `new`：创建新的 active memory；
- `duplicate`：保留一个当前 memory，增加新的 Evidence；
- `replacement`：明确替代时在同一 MemoryItem 追加 active current revision，
  旧 revision 变为 non-current superseded。

完整领域枚举继续保留 `active / superseded / expired / revoked`。本期不自动产生
expired/revoked，但读取路径必须安全排除这些状态。

不明确冲突、Agent 推断出的偏好改变以及无法定位旧对象的替代必须进入 pending，
不能自动改变当前状态。

### FR-10 主动召回

`recall_memory` 接收：

- scenario；
- 当前 query；
- 可选 subject；
- max items 和 token budget。

召回顺序：

```text
trusted owner
  → current active
  → scenario
  → subject
  → task intent / memory type priority
  → simple text relevance
  → threshold
  → item/token limit
```

无相关记忆时返回空结果，不能为填满 Top-K 注入无关内容。

响应同时包含结构化 item 和服务端生成的 `rendered_context`。渲染文本必须说明
记忆是历史上下文，不是当前系统指令；当前用户请求始终优先。

### FR-11 Hook 与 Runner

BeforeRun：

1. 每个用户任务只调用一次 recall；
2. 空结果不注入占位文本；
3. 成功结果把 `rendered_context` 注入 Agent；
4. 失败时默认允许 Agent 在无长期记忆的情况下继续；
5. 不能复用其他用户或 thread 的缓存。

AfterRun：

1. 只处理成功完成的任务；
2. 生成稳定 event id；
3. 提交 user 与 assistant 角色块；
4. 超时后使用相同 event id 有限重试；
5. 捕获失败不能丢失已经生成的 Agent 答案。

Host 没有原生 Hook 时，Runner 必须提供等价顺序。

### FR-12 跨 Agent 共享

- 用户 A / Agent A 捕获的记忆，用户 A / Agent B 可以召回；
- audit metadata 保留不同 client/agent；
- 用户 B / Agent B 不能看到用户 A 的记忆；
- Agent 标识、conversation 标识和工具参数都不能建立 owner。

### FR-13 查看与来源

- `list_memories` 默认只返回当前活动记忆摘要；
- `get_memory` 返回当前 revision 和允许展示的来源；
- 来源包括 conversation、turn、role、message id、tool name、observed time；
- 历史内容只能由显式 history 请求返回并明确标记；
- 默认列表不能把完整证据链发送给每个 Agent。

### FR-14 操作日志

允许记录：

- request/capture/event 的稳定引用；
- owner/client/agent 的不可逆引用；
- 工具名、scenario、policy version；
- status、数量、耗时和错误码。

禁止记录：

- Bearer Token、数据库 URL 和模型 API Key；
- user/assistant/tool 正文；
- memory content 和 source expression；
- 被拦截敏感原文。

## 6. 对外 MCP 工具

| 工具 | Scope | 状态 |
| --- | --- | --- |
| `capture_completed_turn` | `memory:write` | 已实现 |
| `list_memories` | `memory:read` | 已实现 |
| `get_memory` | `memory:read` | 已实现 |
| `list_pending_reviews` | `memory:review` | 已实现 |
| `confirm_pending_memory` | `memory:review` | 已实现 |
| `reject_pending_memory` | `memory:review` | 已实现 |
| `recall_memory` | `memory:read` | 阶段四 |

所有输入 DTO 必须严格拒绝未知字段，特别是 owner、tenant 和 impersonation 字段。
错误使用稳定业务码，不依赖客户端解析内部异常字符串。

## 7. 数据模型

### 7.1 必须保留的语义

| 语义 | 目的 |
| --- | --- |
| owner / tenant / subject / client | 隔离用户与调用 Agent |
| scenario / subject / memory type | 场景分类与对象定位 |
| assertion kind | 区分观点、提供事实、外部事实和推断 |
| Evidence 和 source role | 可解释来源与重复强化 |
| observed / created time | 区分发生时间和系统写入时间 |
| original / normalized time | 保留“下周”等表达及未来计算能力 |
| lifecycle status / is current | 当前、历史、撤销和过期语义 |
| business progress | 支持后续任务型场景 |
| save rationale | 解释准入原因 |
| policy/prompt/schema/model version | 重放、评测和规则演进 |
| recall priority / relation policy | 后续场景扩展边界 |

字段暂未参与 P0 排序不等于没有价值，不能为缩小实现范围删除长期语义。

### 7.2 PostgreSQL

部署环境使用 PostgreSQL 作为唯一权威存储：

- UUID 标识；
- `TIMESTAMPTZ` 时间；
- owner-consistent 复合外键；
- registered scenario/type；
- 单一 current revision 部分唯一索引；
- capture event 部分唯一索引；
- deferred primary Evidence 约束；
- capture、review 和同一 MemoryItem 的 replacement revision 原子事务；
- 版本化 migration 和 checksum。

SQLite 是阶段一至三的原型证据。PostgreSQL 契约与实际 RDS 测试通过后删除，不
作为第二生产后端长期维护。

## 8. 部署需求

目标环境：

```text
可信私网 Agent ── HTTP ───────────────┐
公网 Agent ── HTTPS ── ALB/CLB ──────┤
                                      ▼
                         Linux ECS systemd 服务
  → VPC 私网
  → RDS PostgreSQL
```

- 默认使用 `uv + systemd`，不引入 Docker；
- MCP 本地开发默认监听 `127.0.0.1:8765`；
- ECS 远程接入设置为 `0.0.0.0:8765`，由安全组限制来源；
- TLS 由 ALB/CLB 或等价云入口终止；
- 可信 VPC/VPN Agent 可以直接访问 ECS 私网服务地址；
- PostgreSQL 不开放公网；
- migration 是独立发布步骤；
- secret 使用受限 EnvironmentFile 或等价密钥机制；
- 应用端口不能直接向全网暴露；
- `/health` 验证数据库和 schema，但不返回敏感信息。

详细操作见[阿里云 ECS 远程 MCP 部署](deployment/aliyun-ecs.md)。

## 9. 失败语义

| 情况 | 行为 |
| --- | --- |
| 无效认证 | `unauthenticated`，不进入 Core |
| 缺少 scope | `permission_denied` |
| owner 字段注入 | schema 拒绝 |
| 跨用户 identifier | 与不存在相同 unavailable |
| 相同 event 不同 payload | `idempotency_conflict` |
| 敏感命中 | 不保存原文，只返回 blocked 分类 |
| recall 失败 | Agent 可无记忆继续，不替换为其他用户缓存 |
| capture 失败 | 保留 Agent 答案，使用相同 event id 重试 |
| PostgreSQL 不可用 | 健康检查失败，不降级到 SQLite |
| migration 失败 | 停止发布，不启动新版本 |
| TLS 终止失败 | 不临时开放无认证明文 HTTP |

## 10. 非功能需求

### NFR-01 安全

- 跨用户泄漏在本期测试集上必须为零；
- 禁止敏感原文持久化；
- 公网使用 HTTPS 和认证；
- Token 不放 URL query；
- 数据库只走私网；
- 输入大小、超时和连接池有硬上限。

### NFR-02 可重复

- 固定 MCP SDK 和协议 DTO 版本；
- migration 不可修改历史 checksum；
- 同一 completed event 可重放；
- 自动化测试不依赖真实模型；
- 真实模型不可用时固定 extractor 仍能完成核心演示。

### NFR-03 可观测

- 关键操作有 request id、状态、数量和耗时；
- 健康检查验证数据库连接、必需表、migration 版本和 checksum；
- 错误返回稳定业务码；
- 日志不包含正文、secret 或 backend 异常消息。

### NFR-04 可维护

- Domain/Ports 不依赖 MCP 或 PostgreSQL；
- 只有一个正式 Repository 实现；
- Agent-specific adapter 保持薄；
- 场景差异通过 `ScenarioPolicy` 扩展；
- 未实现能力保留稳定语义和明确激活条件，不预建无验证运行时。

## 11. 验收指标

核心行为：

- MCP 初始化、`tools/list` 和 `tools/call` 成功；
- owner 字段注入被拒绝；
- 四类准入均有确定性案例；
- 同 event replay 不重复写入；
- duplicate 增加 Evidence 而不复制记忆；
- replacement 后旧版本不再召回；
- pending/history/sensitive 内容不注入；
- 用户 A 跨 Agent 共享，用户 B 结果为空；
- PostgreSQL 重启后幂等与隔离仍成立；
- 公网 HTTPS Agent Client 可以完成闭环。

观测指标：

- capture/recall 成功率与 p50/p95 延迟；
- replay 数量；
- 四类准入数量；
- recall result count 和空召回；
- 无关注入率；
- 跨用户和失效记忆错误使用次数。

原型指标只说明当前测试集结果，不等同于生产 SLA 或安全认证。

## 12. 现场演示

建议 5～7 分钟：

1. 展示 ECS systemd 状态、PostgreSQL 健康检查和公网 HTTPS MCP URL；
2. Agent A 连接 MCP，用户 A 表达稳定项目约定、偏好和未完成事项；
3. AfterRun Hook 捕获并展示 auto-save、pending、discard、blocked；
4. Agent B 使用用户 A 的另一 credential，BeforeRun 召回未完成事项；
5. 用户 A 明确替代旧偏好，展示新版本 active、旧版本 superseded；
6. Agent B 再次回答时只使用新偏好；
7. 切换用户 B，执行相同查询并得到空结果；
8. 重放相同 event，证明 extractor 不重复运行；
9. 展示来源、client 区分和无正文操作日志；
10. 说明演示 Token、单实例和无真实敏感数据的原型边界。

## 13. 20 个工作日计划

| 时间 | 工作 | 退出证据 |
| --- | --- | --- |
| D1～D4 | 通用模型、来源、owner 隔离、SQLite 原型 | Core 契约和隔离测试 |
| D5～D8 | 捕获、四类准入、敏感边界、pending | 阶段二验收 |
| D9～D12 | 远程 MCP、认证、管理工具、旧 RAG 清理 | MCP Client/Inspector、跨用户测试 |
| D13～D15 | PostgreSQL、生命周期、主动召回 | RDS 重启幂等、当前/历史过滤 |
| D16～D17 | Hook SDK、两个 Agent、ECS HTTPS 部署 | 跨 Agent 公网闭环 |
| D18～D19 | 真实模型、脚本、延迟与恢复测试 | 10～15 个可重复案例 |
| D20 | 文档、录屏和演练 | 5～7 分钟稳定演示 |

## 14. 当前状态

- 阶段一、二、三已经实现；
- PostgreSQL schema、Repository、migration 命令和 Linux 部署骨架已经加入；
- PostgreSQL 尚需在用户实际 RDS 上运行 contract 与 MCP 重启验收；
- `recall_memory`、duplicate、replacement 和 Hook SDK 尚未实现；
- 完整任务状态以
  [OpenSpec tasks](../openspec/changes/add-general-memory-core/tasks.md)为准。
