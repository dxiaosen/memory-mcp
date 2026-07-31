## Context

本变更把项目从“某个 Agent 进程内的记忆模块”重新定位为独立、平台无关的长期
记忆 MCP 服务。多个 Agent Host 通过同一个认证后的 Streamable HTTP 端点访问
记忆；服务端统一负责可信身份、候选抽取、准入、来源、生命周期、召回和
PostgreSQL 持久化。

截至 2026-07-31，阶段一至五和阶段六的本地延迟/恢复验收已经实现：

- `MemoryItem`、版本化 `MemoryRevision`、`Evidence` 和 owner-scoped Core；
- 四类互斥准入、敏感边界、pending 用户确认和 event 幂等；
- 七个带认证与 scope 的 MCP 工具；
- PostgreSQL migration、连接池、Repository 和重启恢复；
- `general-work` 场景、duplicate Evidence、明确 replacement 和 owner-first
  recall；
- 框架无关 BeforeRun/AfterRun Hook、三份独立 Agent 配置、真实模型抽取与测试
  注入的确定性 fixed adapter；
- systemd 部署模板、真实 PostgreSQL/MCP/Hook/模型本地闭环。

尚未完成的内容只在 `tasks.md` 维护，主要是部署环境的公网 HTTPS/安全组验收、
10～15 条现场脚本、录屏以及最终交付复验。

各制品职责固定如下：

| 制品 | 唯一职责 |
| --- | --- |
| `proposal.md` | 为什么做、范围和影响 |
| `specs/*/spec.md` | 系统必须满足的可观察行为 |
| 本 `design.md` | 技术决策、理由、替代方案和权衡 |
| `tasks.md` | 实施顺序与完成状态 |
| `docs/design.md` | 面向读者的当前系统完整设计 |

本设计不复制逐条 Given/When/Then、操作命令、环境变量表、测试记录或现场脚本。

### 约束

- Python 3.14、uv、官方 MCP Python SDK、Pydantic、psycopg、PostgreSQL；
- `core.domain/application/ports` 不依赖 MCP、HTTP、数据库驱动、Agent SDK、
  LangChain 或运行配置；
- PostgreSQL 是部署环境唯一权威存储；
- owner 只能来自认证后的服务端上下文；
- Memory MCP Server 与 Agent Host 使用相互独立的运行配置，生产模板不得包含
  多身份验收配置、固定候选夹具或破坏性测试数据库配置；
- 普通运行日志不得记录对话、候选、记忆和 Evidence 正文；显式开启内容日志时仅
  记录通过敏感检查后的核心流程内容，Token、Secret 和被敏感规则拦截的原文在
  任何模式下都不得记录；
- 公开工具不得接受 owner、tenant 或 impersonation 参数；
- 当前交付是单实例研究原型，不宣称生产 OAuth、多 worker 或合规认证。

## Goals / Non-Goals

**Goals:**

- 以远程 MCP Server 作为唯一正式产品入口；
- 支持不同 Agent Host 共享同一用户的长期记忆，同时严格隔离不同用户；
- 在顶层任务开始前确定性召回，在成功得到最终结果后确定性捕获；
- 让模型只提出不可信结构化候选，所有身份与准入决定仍由程序控制；
- 保持来源、认识论标签、幂等、当前/历史和用户确认边界；
- 使用 PostgreSQL 提供跨进程重启的权威状态和事务一致性；
- 允许本地/私网直接访问服务地址，公网由云负载均衡终止 HTTPS；
- 保持测试专用 fixed adapter，使自动化验证可重复且不依赖外部模型；
- 让生产服务模板默认走真实抽取并在缺少凭据时启动失败；
- 通过依赖方向和小型 adapter 保持 Agent、模型 provider 与存储可替换。

**Non-Goals:**

- Agent 编排、Agent 间消息总线或共享任务调度；
- 生产 OAuth 授权服务器、组织目录、团队共享记忆或数据库级 RLS；
- Redis/Kafka、durable queue、多 worker 自动伸缩或高并发容量承诺；
- Embedding、向量数据库、HNSW、复杂关系图和通用场景 DSL；
- 自动过期、完整 revoke/delete/suppression 和合规级审计；
- 第二个正式业务场景、Web 管理后台、MCP Apps；
- Docker、Kubernetes、Nginx 或特定 Agent 平台运行依赖；
- 真实敏感数据接入或对用户陈述进行事实验证。

## Decisions

### 1. MCP Server 是唯一产品边界

```text
Agent Host A ── Hook / MCP Client ─┐
Agent Host B ── Hook / MCP Client ─┼─ Streamable HTTP ─ Memory MCP
Agent Host C ── direct MCP tools ──┘                       │
                                                           ▼
                                                  PostgreSQL + Model
```

MCP transport 是 Core 的外层 adapter，但只有通过 MCP 暴露的能力才构成正式产品
契约。Core 的 Python 方法用于服务端内部组合和测试，不作为远程用户 API。

理由：

- 不同 Agent 进程可复用同一记忆生命周期；
- 身份、准入、敏感处理和持久化集中执行；
- Agent Client 不需要数据库权限或模型抽取密钥；
- 服务生命周期与任意 Agent 进程解耦。

拒绝的替代方案：

- 每个 Agent 内嵌一套 Memory Core：产生多份状态和规则漂移；
- Agent 直接访问 PostgreSQL：泄漏身份/事务边界并耦合 schema；
- 依赖特定 Agent 平台：削弱 MCP 的平台无关价值。

### 2. Core 使用内向依赖，基础设施实现端口

```text
server ──────────────┐
scenarios ───────────┼──> core.application ──> core.domain / core.ports
postgresql adapter ──┘                               ▲
extraction adapter ──────────────────────────────────┘

hooks ──> remote MCP only
```

`core.domain`、`core.application` 和 `core.ports` 不读取环境变量，也不导入 MCP、
HTTP、LangChain 或 psycopg。`server` 是组合根；`hooks` 是远程消费者，
不能导入 Repository。

理由是让领域契约能用 InMemory/Fake 快速测试，同时通过同一端口验证 PostgreSQL
和真实模型。依赖守卫测试防止便利 re-export 或反向导入重新耦合各层。

### 3. 正式传输使用 Streamable HTTP

正式 MCP 路径为 `/mcp`，健康检查为独立 `/health`。服务采用 stateless HTTP；
MCP Client 可以来自同机、VPC/VPN 或公网 HTTPS。

部署选择：

- 本地与可信私网直接访问 `http://服务地址:8765/mcp`；
- ECS 监听 `0.0.0.0` 时由安全组限制来源；
- 公网使用 ALB/CLB 等云负载均衡终止 HTTPS，再转发到 ECS 私网端口；
- PostgreSQL 只允许私网访问；
- Python 进程由 `uv + systemd` 管理。

不引入 Nginx、Docker 或 Kubernetes，因为当前单实例服务不需要额外应用层代理与
容器编排。stdio 不作为产品入口；若用于局部调试，也不能替代远程契约验收。

### 4. owner 与认证客户端身份分离

认证适配器把 Bearer Token 映射为：

```text
RequestPrincipal
├── tenant_id
├── subject_id
├── owner_key = tenant_id + ":" + subject_id
├── client_id
└── scopes: read / write / review
```

`owner_key` 决定数据隔离，并且只能由校验后的 `tenant_id` 和 `subject_id`
确定性派生，不能作为静态 Token JSON 中另一份容易漂移的配置。两个身份分量只
允许字母、数字、点、下划线和连字符，因此 `tenant_id:subject_id` 无歧义。

`client_id` 只描述认证客户端，不参与 owner 隔离。OAuth/OIDC 适配器应从已验证
Token 或 introspection 结果取得它；当前不透明静态 Bearer Token 不携带可验证
claim，因此 `StaticTokenVerifier` 使用凭据的单向摘要生成稳定、不可还原的
`static-…` 审计引用。Token 轮换会产生新的客户端引用，这是静态原型边界下的
预期行为。

`agent_id` 不是 MCP 或 OAuth 标准身份，且当前实现中与 `client_id` 含义重叠、
只出现在日志，所以删除。未来只有在“同一 OAuth client 下确实存在多个可信 Agent
实例”且授权服务器提供可验证 claim 时才重新引入。用户 A 的不同客户端可以映射
到同一个 owner，用户 B 使用相同应用类型时仍必须映射到不同 owner。

工具 schema 不接受 owner、tenant、subject identity 或 impersonation 参数；
`PrincipalContext` 只由 Server 构造。Repository 的每个用户数据查询仍显式携带
owner 条件，不能只依赖 transport 层检查。

当前环境变量 Token 映射只配置 `tenant_id`、`subject_id` 和 `scopes`，是原型
认证，不等同于生产 OAuth。一个无法提供可信最终用户身份的共享应用 Token 只能
作为单 owner 原型，不能宣称多用户隔离。

运行时采用中性命名：

- `MEMORY_MCP_AUTH_TOKENS`：静态 Token 到 tenant/subject/scopes 的受保护
  JSON 映射；
- `ConfiguredPrincipal`：一条配置化身份记录；
- `StaticTokenVerifier`：当前可替换的 Bearer Token 校验适配器。

名称中不使用 `demo` 或 `test`，但中性命名不改变其安全能力边界。静态 Token
必须是至少 32 个字符的独立高熵值；该最低长度检查只能防止明显占位符，不能把
静态映射升级为生产 OAuth/OIDC。

### 5. 使用版本化完成轮次事件

AfterRun 提交 `CompletedTurnEventV1`：

```text
contract_version = "1"
event_id
scenario
conversation_id
turn_id
observed_at
subject_hint?
messages[]
  role = user | assistant | tool
  content
  message_id?
  tool_name?
```

事件不包含 owner。角色必须保留，因为用户陈述、助手输出和工具结果的认识论地位
不同：用户的明确表达可以成为自动保存证据，assistant/tool 内容不能自动升级为
用户观点。

未知主版本返回稳定错误，不猜测字段语义。`subject_hint` 是不可信提示，不能覆盖
owner 或来源；`source_expression` 必须能在脱敏后的来源块中定位。

拒绝继续使用单段无角色文本作为远程契约，因为它无法证明某条观点来自用户还是
Agent。

### 6. 七个 MCP 工具组成最小闭环

写入与召回主路径：

- `capture_completed_turn`
- `recall_memory`

查看与治理路径：

- `list_memories`
- `get_memory`
- `list_pending_reviews`
- `confirm_pending_memory`
- `reject_pending_memory`

所有工具使用严格 Pydantic DTO、拒绝额外字段、返回结构化 receipt 和稳定错误码。
跨 owner identifier 与不存在必须不可区分。暂不增加 correction/revoke/delete、
usage report 或场景管理工具，因为它们不阻塞当前跨 Agent 闭环。

具体 MUST/SHALL 与场景只在四份 capability spec 中维护。

### 7. Hook 以顶层用户任务为生命周期边界

```text
BeforeRun
  await recall_memory
  注入可空 rendered_context
        │
        ▼
顶层 Agent run（可包含模型、工具、子 Agent、重试）
        │
        ▼
成功产生 final output
  await capture_completed_turn
AfterRun
```

BeforeRun/AfterRun 均只触发一次。AfterRun 的时机是一次顶层用户任务成功结束，
不是内部每次 LLM/tool/sub-agent 调用结束，也不是整个 conversation 关闭。
取消、异常或没有 final output 时不执行成功捕获。

Bridge 以 `(scenario, conversation_id, turn_id)` 作为 run key：

- 相同 key 和相同 payload 复用同一执行结果；
- 相同 key 和不同 payload 立即报 typed conflict；
- 完成 receipt 使用有界进程内 cache；
- Capture event ID 由 run key 稳定生成；
- MCP Client 复用 HTTP 连接池并显式关闭。

### 8. 异步 Hook 不等于消息队列

Hook 使用 coroutine，避免阻塞 Agent 事件循环。BeforeRun 必须等待召回；默认
Runner 等待 AfterRun receipt，以便获得 capture summary、replay 和 failure code。

当前不引入外部队列，原因是：

- 单次 capture 是有界模型/数据库请求；
- Bridge 有有界重试；
- Server 已通过稳定 event 和 PostgreSQL 事务保证最终幂等；
- 当前没有多进程削峰、离线重放或崩溃后可靠投递要求。

Host 可以在发出 final response 后调度 AfterRun，但普通 `create_task` 可能随进程
退出而丢失，不能描述为可靠队列。若未来要求崩溃后投递、多进程统一削峰或离线
重放，应引入 durable outbox + queue worker；worker 仍调用现有幂等 capture
边界，不能绕过 MCP 身份或 Repository 事务。

### 9. 模型只发现候选，不做可信决策

组合根始终创建一个 `CandidateExtractor`：

- 生产运行时：通过 LangChain 调用受支持的 OpenAI/DeepSeek provider，返回严格
  `CandidateBatch`；
- 自动化测试：通过 `candidate_extractor` 依赖注入使用精确匹配
  `source_expression` 的 `FixedCandidateBackend`，不读取运行时环境。

模型输入只包含脱敏后的完成轮次和当前 ScenarioPolicy，不包含 owner、Token、
DSN 或 API Key。模型可以建议 subject、memory type、assertion kind、durability、
内容和理由；程序决定 owner、来源、观察时间、准入、生命周期目标和权限。

模型输出依次经过：

```text
Pydantic schema
→ 原文 evidence 检查
→ 场景 memory type 检查
→ 所有持久化文本敏感检查
→ 确定性准入
→ PostgreSQL 事务
```

真实模型配置不完整时服务启动失败，不在运行中静默切换测试 adapter。DeepSeek V4 的
thinking 模式与 LangChain 强制 schema tool choice 不兼容，因此 DeepSeek
extraction adapter 固定关闭 thinking；抽取不依赖 chain-of-thought。

### 10. 四类准入互斥且由程序控制

每个候选必须得到一个结果：

| 结果 | 含义 |
| --- | --- |
| `auto_save` | 明确、持久、允许且有可信用户证据 |
| `pending` | 弱推断、含糊冲突或不确定替代，需要用户确认 |
| `discard` | 临时、无长期价值或不符合场景 |
| `blocked` | 命中敏感/禁止持久化边界 |

pending 与 blocked 内容不能进入普通召回。敏感检测在模型调用前和持久化前各执行
一次，覆盖 subject、content、source expression、rationale、时间表达、message id
和 tool name 等所有可能落库的自由文本。

服务日志和错误响应不包含 backend 异常消息或被拦截正文，只返回稳定错误类型、
类别、数量和引用。

### 11. PostgreSQL event 与事务提供最终一致性

Capture 以 `(owner, event_id, payload fingerprint, policy version)` 建立幂等边界：

- 相同 event、相同 payload 返回原逻辑 receipt，并标记 replay；
- 相同 event、不同 payload 返回 `idempotency_conflict`；
- 重叠请求至多一次执行抽取和逻辑提交；
- 可重试失败进入 `reprocess_required`，恢复后使用同一 event 继续；
- capture run、outcome、Memory、Evidence 和 pending 在一个事务中提交。

Bridge cache 只优化单进程重复调用；跨进程、重启和网络不确定性的最终幂等由
PostgreSQL 保证。

拒绝仅依赖内存锁或数据库唯一约束异常，因为前者无法跨进程，后者不能表达安全
replay receipt 与 payload conflict 的区别。

### 12. PostgreSQL 是唯一部署权威

PostgreSQL 保存：

- registered scenario/type；
- owner-scoped MemoryItem、revision 和 Evidence；
- capture event、fingerprint 和无正文 outcome；
- pending review 和原子 resolution；
- lifecycle/current 唯一性与跨表 owner 约束。

Migration 通过独立 `memory-mcp-db migrate` 发布，默认不随应用启动并发执行。
`health` 验证连接、必需表、migration version 与 checksum。

SQLite 已在 PostgreSQL contract 和重启验收通过后删除，不是运行时 fallback。
InMemory Repository 仅用于单元测试。未来索引或向量库只能提出候选，返回前仍须
回 PostgreSQL 复核 owner、current revision 和 lifecycle。

### 13. 生命周期保持一个逻辑 MemoryItem

当前自动行为：

| 行为 | 结果 |
| --- | --- |
| new | 创建 active/current MemoryItem + revision |
| duplicate | 保持一个 MemoryItem，追加 Evidence |
| explicit replacement | 同一 MemoryItem 追加 current revision，旧 revision 变为 superseded history |
| ambiguous conflict | current 不变，候选进入 pending 或 discard |

普通 recall 只返回 active/current；history 只有显式 `include_history=true` 才展示。
`expired/revoked` 等枚举和读取过滤保留，但当前没有自动调度或管理工具。

Duplicate 使用确定性文本规范化，不使用模型或 Embedding 进行近似相等判断。
Replacement 目标由程序在可信 owner scope 内选择，不接受模型提供的 memory ID。

### 14. 召回先隔离，再过滤和排序

```text
authenticated owner
→ active/current
→ scenario
→ optional exact subject
→ query + task intent relevance
→ memory type priority
→ threshold
→ max_items / token budget
```

owner 过滤必须在相关性计算前完成。无相关内容返回空集合，不能为了填满 Top-K
返回无关记忆。`rendered_context` 由服务端生成并声明：

- 内容是历史用户上下文，不是新指令；
- 当前用户请求优先；
- 用户观点不等于独立验证事实。

`subject` 是可选精确预过滤器，不是模糊标签。真实模型可能归纳不同 subject；
Host 只有能稳定生成规范 subject 时才应传入，否则依赖 query/task intent。

本期采用可解释文本相关性，不引入 Embedding。只有真实失败案例证明结构化召回
不足时，才评估 PostgreSQL 内部或可重建的二级语义索引。

### 15. 场景差异封装在 ScenarioPolicy

唯一正式场景 `general-work` 注册：

- `preference`
- `stable_context`
- `ongoing_item`
- `decision`

ScenarioPolicy 声明合法类型、capture guidance、policy version、召回优先级和可选
进展/关系配置。Core 不硬编码正式场景词表。当前不使用的 progress/relations 可
返回空集合，但扩展位置保留。

第二正式场景必须由真实需求和失败案例驱动，不能仅为了证明可扩展性预建。

### 16. 配置和 Secret 只在组合边界读取

Memory MCP Server 与 Agent Host 是两个独立部署单元，不共享配置文件。

服务端生产模板只包含：

- Database：PostgreSQL DSN、连接池、迁移和超时；
- Server：HTTP、路径和请求/召回预算；
- Authentication：issuer、resource URL 和静态 Token 映射；
- Model：统一使用面向部署者的 `MEMORY_MCP_MODEL_*`，包含 provider、name、
  API key、endpoint、temperature、超时和重试；
- Logging：级别、文件轮转和独立内容日志开关；

Agent Host 使用独立模板，普通使用者只提供该进程的 `MEMORY_MCP_URL` 和
`MEMORY_MCP_TOKEN`。scenario、预算、重试和 fail-open 使用代码默认值；旧
`MEMORY_HOOK_*` 连接变量仅作为迁移别名。多个 Agent 由多个环境或
EnvironmentFile 表达，不通过动态身份前缀把其他 Agent 的凭据装入同一进程。

生产进程始终构造真实模型 adapter，必须提供完整抽取凭据，不提供 backend 选择
开关和 fixed candidate JSON。确定性 fixed adapter 及其候选 payload 只由测试
代码显式构造和注入。测试数据库 URL 也只由测试进程显式注入。

配置实现拆成 `MemoryServerSettings` 和 `ExtractionSettings`，前者只负责数据库、
HTTP、认证和日志，后者负责候选抽取使用的完整模型配置。`ExtractionSettings`
是内部职责名，环境变量使用更直观的 `MODEL`；字段使用 `model_name`，避免出现
`MODEL_MODEL`。构造参数用于测试显式注入，进程环境变量覆盖可选的本地 env file。
DSN、Token 和 API Key 均按 Secret 处理。

DSN、Token 和模型 Key 不得进入仓库、systemd unit、命令行参数、MCP URL 或日志。
运行配置文件必须限制权限；公网部署要替换为正式授权边界。

### 17. 日志默认记录元数据，可显式开启内容跟踪

普通模式允许记录：

- request/capture/event 的稳定摘要；
- owner/client 的稳定假名引用；
- tool、scenario、policy version；
- status、error code、数量、耗时和 retry/replay。

`MEMORY_MCP_LOG_CONTENT=false` 是代码和模板默认值。设置为 `true` 后，以独立的
`memory_mcp.content` logger 在 INFO 级别增加：

- `memory.capture.input`：敏感检查后的角色消息与 TurnEnvelope；
- `memory.capture.candidates`：通过敏感检查的结构化候选；
- `memory.capture.admission`：候选的决定、reason、memory/review 结果；
- `memory.capture.persisted`：事务提交后的 CaptureResult；
- `memory.recall.ranked`：query、task intent、候选内容和相关性分数；
- `memory.recall.output`：选择结果与 rendered context。

内容模式启用时必须在进程启动打印 WARNING。Bearer Token、DSN、API Key、blocked
原文和 backend 异常正文始终禁止；因此 capture 输入在敏感预检后记录，候选只在
持久化前敏感检查通过后记录。内容开关只影响日志，不改变准入、权限和持久化。

同步 Core、模型和 Repository 工作通过 worker thread 执行，不直接阻塞 MCP
事件循环。

### 18. 目录按语义包拆分，不按每个类建层

```text
src/memory_mcp/
├── core/
│   ├── domain/
│   ├── application/
│   ├── ports/
│   └── adapters/postgresql/
├── extraction/
├── scenarios/
├── server/
│   └── tools/
├── hooks/
├── db.py
└── logging.py
```

`server/tools` 按 capture/memory/recall/review 拆分；其余 Server 文件职责单一且体量
有限，不再增加 `api/transport/mcp` 等重复目录。`extraction` 统一真实/固定模型
适配能力，但运行时 factory 只组合真实模型；fixed backend 保留在同一契约层供
测试注入。settings、provider factory、backend/schema 和 composition 分文件。

PostgreSQL Repository 保留单一 facade 与事务边界，row mapping、write validation
和 schema/migration 分离。CaptureService 保留公开用例 facade，候选处理和 Review
协调作为内部协作服务。包 `__init__` 保持轻量，避免导入完整 app 或驱动。

### 19. 开发者注释以中文为主，稳定外部契约保持原文

模块、类、函数 docstring 和解释非显然业务约束的源码注释统一使用中文，帮助本
项目的主要维护者从代码直接理解边界。注释说明“为什么”和契约约束，不逐行复述
实现，也不为了追求中文覆盖率增加无信息量注释。

以下内容不得仅为了语言统一而翻译：Python/MCP/OAuth/PostgreSQL 等标准标识，
对外 MCP 工具 description、Pydantic/JSON 字段、错误码、日志事件名、模型 system
prompt、SQL 和第三方 API 参数。这些字符串属于协议、运行数据或模型契约，不是
开发者注释，随意翻译会破坏兼容性或行为。

## Risks / Trade-offs

- **[Agent Host 能连接 MCP 但没有稳定 Hook API]** → 区分“工具兼容”与“自动
  Before/After”；无原生 Hook 时使用外层 Runner。
- **[原型 Token 被误解为生产身份]** → README、配置和部署文档显式标注边界；
  公网验收不得宣称生产 OAuth；拒绝短于 32 字符的明显占位 Token。
- **[服务端与 Agent 配置混放导致凭据扩散]** → 两个独立模板、固定命名空间和
  单进程单 Agent 配置；多身份矩阵只存在于测试与验收说明。
- **[内容日志泄漏测试正文]** → 默认关闭、独立显式开关、启动 WARNING；只允许
  在受控手工环境开启，使用完立即关闭并清理日志。
- **[同用户多 Agent 映射错误]** → Server 由已校验 tenant/subject 唯一派生
  owner，并保留 A/Agent A、A/Agent B、B/Agent B 矩阵测试。
- **[模型产生无效或敏感候选]** → schema、原文证据、场景和敏感检查全部在程序
  边界执行；失败不保存半成品。
- **[Hook 重试造成重复]** → 稳定 event、payload fingerprint、进程内去重和
  PostgreSQL 幂等共同保护。
- **[召回上下文成为提示注入载体]** → 服务端安全渲染、当前请求优先、历史内容
  只作为数据。
- **[无向量检索导致中文或语义召回不足]** → 先收集失败案例；新增索引后仍回
  PostgreSQL 权威复核。
- **[AfterRun 在用户响应关键路径增加延迟]** → 默认等待 receipt 以获得确定状态；
  Host 可接受丢失风险后置调度，可靠投递需求再引入 durable queue。
- **[公网端点扩大攻击面]** → HTTPS、逐请求认证、受限安全组、私网 PostgreSQL 和
  Secret 注入；部署证据在阶段六单独验收。
- **[真实模型或云服务影响现场稳定性]** → 测试注入的 fixed adapter、本地测试库、
  锁定依赖和录屏提供自动化与展示证据，但不伪装为生产运行时故障降级。
- **[单实例限制吞吐和可用性]** → 当前不宣称 SLA；通过真实负载数据决定是否引入
  worker、queue 或连接池扩容。

## Migration Plan

### 已完成的产品迁移

1. 保留领域 Core，在外层增加 MCP transport 与可信认证；
2. 将无角色 TurnEnvelope 适配为版本化完成轮次 DTO；
3. 用 MCP Server 和最小客户端替代旧 RAG/CLI 产品入口；
4. 将仍需的模型能力收敛到 `extraction`；
5. 以 PostgreSQL migration/Repository 替换 SQLite 运行路径；
6. 增加生命周期、owner-first recall 和 Hook SDK；
7. 增加 systemd、配置、测试和端到端文档。

这些步骤不迁移真实用户数据；SQLite 原型只提供历史行为证据，已退出运行路径。

### 剩余部署步骤

1. 在目标 ECS/RDS 环境注入受保护配置并显式执行 migration；
2. 验证本机 health、私网 PostgreSQL 和 systemd 生命周期；
3. 配置 ALB/CLB HTTPS，并确认 ECS 应用端口不向公网开放；
4. 从目标 Agent 网络执行 tools/list、capture、recall 和隔离反例；
5. 完成现场脚本、延迟/恢复证据、录屏和最终复验。

具体完成状态只看 `tasks.md`。

### 回滚

- 应用回滚到上一个代码版本并重新同步锁定依赖；
- 已成功执行的向前兼容 migration 不做破坏性降级；
- migration 失败时停止发布，不启动新服务；
- 真实模型失败时停止 capture 并修复配置或上游服务，不在生产进程切换测试
  adapter；
- TLS 入口失败时保持应用端口受限，不临时开放公网明文 HTTP；
- Agent adapter 失败时仍可用 MCP Client/Inspector 验证服务端契约。

## Open Questions

以下问题不阻塞当前单实例闭环：

1. 生产认证采用哪一个 OAuth/OIDC 授权服务器，以及 tenant/subject claims 规范；
2. 哪类真实召回失败足以触发 subject 规范化或语义索引；
3. 何种吞吐、可靠投递或多进程需求触发 durable outbox/queue；
4. 是否需要 correction/revoke/delete 与更完整的生命周期治理工具；
5. 第二正式场景及其 memory types、progress 和 relation 规则；
6. 多 worker 前是否先增加数据库级 RLS、分布式限流和更细审计。
