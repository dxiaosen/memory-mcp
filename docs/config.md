# Memory MCP 配置参考

本文是运行时配置的单一参考。设计原因见[整体设计](design.md)，实际操作见
[端到端使用](usage.md)。

## 1. 配置边界

Memory MCP 有两个独立部署单元，不能共用一份“全家桶”配置：

| 部署单元 | 发行包 | 模板 | 内容 |
| --- | --- | --- | --- |
| Memory MCP Server | `memory-mcp` | `server/.env.example` | 数据库、HTTP、认证、抽取、日志 |
| 一个 Agent Host | `memory-mcp-agent` | `agent/.env.example` | MCP URL、该 Agent 的 Token |

一个 Server 可以认证多个 Agent，但一个 Agent 进程只应持有自己的
地址和 Token。多 Agent 部署需要复制多份 Agent 配置，分别注入各自进程；不应让
Agent A 的进程读取 Agent B 的凭据。`profile_id`、owner、client/Agent ID、Hook
预算和重试不要求普通用户配置。

两个发行包的生产依赖相互独立。Server 不提供 `memory-mcp-hook`；Agent 包只提供
Hook 命令和 `memory_mcp_agent` API，不安装数据库、LangChain、模型 Provider、
ASGI Server 或 migration 命令。仓库开发时才通过 uv workspace 把二者装进同一个
`.venv`。

`server/.env.example` 是生产形态的服务端模板，因此：

- 只包含真实模型抽取配置；
- 只展示一个正式 Principal，不内置三身份测试矩阵；
- 不包含模型 backend 选择器或 fixed candidate fixture；
- 不包含 Agent Hook 配置或测试数据库配置。

`agent/.env.example` 只展示一个 Agent Host。多身份验收所需的额外身份、测试
候选和测试数据库均由测试代码显式创建，不能混入生产配置。

## 2. 加载顺序与 Secret

服务端 `MemoryServerSettings` 和 `ExtractionSettings` 在本地运行时读取项目根目录
`.env`；进程环境变量会覆盖文件值。生产环境推荐直接由 Secret Manager、systemd
`EnvironmentFile` 或编排平台注入。

`MemoryHookSettings` 不会隐式读取项目根目录 `.env`。生产 Agent Host 从自己的
进程环境读取 `MEMORY_MCP_URL` 和 `MEMORY_MCP_TOKEN`；仓库里的示例命令仅通过
`--env-file examples/agent.env` 显式加载某一个 Agent 的文件，避免跨 Agent
泄露配置。

Pydantic Settings 的有效优先级为：

1. 显式构造参数，主要用于依赖注入和测试；
2. 进程环境变量；
3. 显式或默认指定的 env 文件；
4. 代码默认值。

本地文件至少限制为当前用户可读：

```bash
cp server/.env.example .env
chmod 600 .env
cp agent/.env.example examples/agent.env
chmod 600 examples/agent.env
```

`.env` 和 `*.env` 已被 Git 忽略。不得提交或打印真实数据库 DSN、Bearer Token
和模型 API Key。

PostgreSQL URI 的用户名或密码若包含保留字符必须 percent-encode，例如 `@` 写为
`%40`，`:` 写为 `%3A`，`/` 写为 `%2F`，`?` 写为 `%3F`，`#` 写为 `%23`，
`%` 写为 `%25`。

## 3. 哪些固定，哪些可配置

| 类别 | 当前性质 | 说明 |
| --- | --- | --- |
| Core 领域规则与四类准入 | 代码固定 | owner-first、Evidence、revision、pending、敏感拦截和幂等不能由环境变量绕过 |
| 内置 MemoryProfile | 代码固定 | `general-work` 与 `investment-research` 的类型、捕获规则、版本、优先级和元数据策略 |
| MCP 工具与 DTO v1 | 代码固定 | 八个工具；capture contract version 为 `1` |
| PostgreSQL schema | migration 管理 | 通过独立命令升级，不在服务启动时动态拼表 |
| Server 地址、连接池和预算 | 环境可配置 | 有类型与范围校验 |
| Principal 映射 | 静态环境配置 | 当前正式认证入口；可替换为 OAuth/OIDC 适配器 |
| 抽取 provider/model | 环境可配置 | 生产始终使用真实模型，不提供 fixed 运行时开关 |
| Agent 连接 | 每个 Agent 独立配置 | 只要求 URL 和 Token |
| Agent Hook 策略 | 代码默认 | `profile_id`、超时、fail-open、召回预算、capture 重试和状态 TTL |
| In-memory Repository、fake/fixed extractor | 仅自动化测试 | 通过依赖注入使用，不属于部署路径 |

PostgreSQL MCP 端到端测试会在测试代码里注入确定性 fixed extractor；它只替代
候选生成，真实执行 MCP transport、鉴权、Core、PostgreSQL、Hook 和 owner 隔离。

## 4. Server 配置

### 4.1 PostgreSQL

| 完整变量名 | 代码默认值 | 必需 | 说明 |
| --- | --- | --- | --- |
| `MEMORY_MCP_DATABASE_URL` | 无 | 是 | PostgreSQL DSN；Secret |
| `MEMORY_MCP_DATABASE_POOL_MIN_SIZE` | `1` | 否 | 连接池最小连接数，范围 1–50 |
| `MEMORY_MCP_DATABASE_POOL_MAX_SIZE` | `5` | 否 | 最大连接数，范围 1–100，且不小于 min |
| `MEMORY_MCP_DATABASE_CONNECT_TIMEOUT_SECONDS` | `10` | 否 | 建连/取连接超时，最大 300 秒 |
| `MEMORY_MCP_DATABASE_MIGRATE_ON_STARTUP` | `false` | 否 | 本地可临时开启；部署应使用独立 migration 步骤 |

生产数据库连接应按基础设施要求启用 TLS，例如在 DSN 中配置
`sslmode=require`。破坏性测试必须使用独立、可清空且名称包含 `test` 的 database。

### 4.2 HTTP 与资源预算

| 完整变量名 | 代码默认值 | 说明 |
| --- | --- | --- |
| `MEMORY_MCP_HOST` | `127.0.0.1` | 本机默认；私网监听可设为 `0.0.0.0` 并限制安全组 |
| `MEMORY_MCP_PORT` | `8765` | 监听端口 |
| `MEMORY_MCP_MCP_PATH` | `/mcp` | Streamable HTTP 路径 |
| `MEMORY_MCP_HEALTH_PATH` | `/health` | 健康检查路径，不能与 MCP path 相同 |
| `MEMORY_MCP_STATELESS_HTTP` | `true` | 无状态 HTTP session |
| `MEMORY_MCP_MAX_CAPTURE_CHARACTERS` | `100000` | 单个完成轮次最大字符数，范围 1,000–1,000,000 |
| `MEMORY_MCP_RECALL_MAX_ITEMS` | `10` | 服务端召回条数硬上限，范围 1–10 |
| `MEMORY_MCP_RECALL_MAX_TOKEN_BUDGET` | `1200` | 服务端渲染预算硬上限，范围 64–8,000 |

同一 VPC/VPN 内可以直接访问 `http://host:port/mcp`，不需要 Nginx。公网场景应由
ALB/CLB 等入口终止 HTTPS，再转发到受限的 ECS 私网端口。

### 4.3 静态认证

| 完整变量名 | 代码默认值 | 说明 |
| --- | --- | --- |
| `MEMORY_MCP_AUTH_ISSUER_URL` | `http://localhost/memory-mcp-auth` | MCP auth metadata issuer |
| `MEMORY_MCP_RESOURCE_SERVER_URL` | 未设置 | 未设置时根据请求推导 MCP resource URL |
| `MEMORY_MCP_AUTH_TOKENS` | `{}` | Token 到可信 Principal 的 JSON 映射；为空拒绝启动；Secret |

`MEMORY_MCP_AUTH_TOKENS` 的值是 JSON object。变量名不重复描述序列化格式；每个
key 是至少 32 字符的独立高熵
Token，每个 value 包含：

| 字段 | 必需 | 含义 |
| --- | --- | --- |
| `tenant_id` | 否 | 默认 `default` |
| `subject_id` | 是 | 授权系统中的最终用户身份 |
| `scopes` | 否 | 默认 read/write/review，可收窄 |

生产标识应来自稳定的内部身份体系，不使用显示名、邮箱或 `user-a` 之类临时标签。
推荐形态：

```json
{
  "<random-token>": {
    "tenant_id": "tenant-001",
    "subject_id": "subject-001",
    "scopes": ["memory:read", "memory:write", "memory:review"]
  }
}
```

这里的 `001` 只是部署模板占位符，正式环境应替换为授权系统提供的不可变 ID。
两个 ID 只允许字母、数字、点、下划线和连字符。服务端固定派生
`owner_key = tenant_id:subject_id`，它一经写入记忆数据就不能随显示名、Token 或
Agent 变化。同一用户的多个 Token 因而自然共享 owner，不同 subject 自然隔离。
MCP 工具参数从不接受 owner，调用方无法覆盖该派生规则。

当前 Token 是不透明静态凭据，MCP SDK 无法从中自动识别业务客户端。校验器会对
Token 做 SHA-256 单向摘要并截取为 `static-…` client ID，只用于日志审计，不参与
授权和 owner 隔离，也不会暴露原 Token。Token 轮换会产生新的审计 client ID。
`agent_id` 不是 MCP/OAuth 标准字段，当前没有独立授权语义，因此不再配置。未来
OAuth/OIDC 适配器应直接使用已验证 Token 或 introspection 返回的 `client_id` 和
`subject`。

当前实现是静态 Bearer Token 认证适配器，不提供动态签发、自动过期、完整
revoke/delete、轮换编排或 OAuth/OIDC federation。它适合受控环境和第一阶段上线；
需要这些能力时应替换认证适配器，而不是继续扩充静态 JSON。

### 4.4 模型与候选生成

抽取由内部 `ExtractionSettings` 负责，但仍属于 Server 部署单元。面向部署者的
变量统一使用更直观的 `MEMORY_MCP_MODEL_` 前缀。

| 完整变量名 | 代码默认值 | 必需 | 说明 |
| --- | --- | --- | --- |
| `MEMORY_MCP_MODEL_PROVIDER` | `deepseek` | 否 | `deepseek` 或 `openai` |
| `MEMORY_MCP_MODEL_NAME` | 无 | 是 | provider 可用的模型 ID |
| `MEMORY_MCP_MODEL_API_KEY` | 无 | 是 | 模型凭据；Secret |
| `MEMORY_MCP_MODEL_BASE_URL` | provider 默认 | 否 | 官方或兼容 Chat Completions 地址 |
| `MEMORY_MCP_MODEL_TEMPERATURE` | `0` | 否 | 范围 0–2 |
| `MEMORY_MCP_MODEL_TIMEOUT_SECONDS` | `60` | 否 | 单次模型调用超时，最大 300 秒 |
| `MEMORY_MCP_MODEL_MAX_RETRIES` | `2` | 否 | provider 层重试，范围 0–10 |

生产进程始终构造真实模型 extractor。缺少 model 或 API key 时服务在启动阶段
失败，不会静默退化为测试替身。运行时没有 backend 选择器和固定候选 JSON。

真实模型只接收脱敏后的记忆配置、subject hint、时间和本轮正文，不接收 owner、Token
或 DSN。输出还必须经过结构 schema、原文 Evidence、配置类型、敏感边界和准入规则
的二次校验。

DeepSeek V4 默认 thinking 与 LangChain 的强制 schema tool choice 不兼容，当前
DeepSeek extraction adapter 固定关闭 thinking。OpenAI provider 不应用该参数。

### 4.5 日志

| 完整变量名 | 代码默认值 | 说明 |
| --- | --- | --- |
| `MEMORY_MCP_LOG_LEVEL` | `INFO` | `DEBUG/INFO/WARNING/ERROR` |
| `MEMORY_MCP_LOG_CONTENT` | `false` | 是否记录通过敏感检查后的业务内容 |
| `MEMORY_MCP_LOG_FILE` | `.memory-mcp/logs/memory-mcp.log` | 日志文件 |
| `MEMORY_MCP_LOG_MAX_BYTES` | `10485760` | 单文件轮转阈值 |
| `MEMORY_MCP_LOG_BACKUP_COUNT` | `5` | 保留的轮转文件数 |

将 `MEMORY_MCP_LOG_FILE` 设为空字符串会关闭文件 handler，只保留 stderr/systemd
journal。

默认日志只记录稳定引用、工具、阶段、状态、数量、错误码和耗时。手工联调可短期
开启 `MEMORY_MCP_LOG_CONTENT=true`，记录经过日志清洗后的输入、候选、准入、
持久化和召回内容。Bearer Token、DSN、API Key、异常正文和敏感规则拦截的原文在
任何模式下都不应写入日志。

## 5. Agent Host 配置

### 5.1 安装与协议边界

正式 Agent Host 只安装版本化 `memory-mcp-agent` wheel，或从组织 registry 安装
固定版本。它以最小 JSON-RPC Client 调用 Memory MCP Server 固定启用的
Streamable HTTP JSON response，不依赖完整 MCP SDK。这个实现只覆盖主动记忆所需
的 initialize 和 tool call；Agent 宿主原有的普通 MCP 连接仍由宿主自身负责。

服务端与 Agent 的兼容性通过真实 HTTP/MCP 集成测试验证。若未来改变 Server
transport 或关闭 JSON response，必须先升级并验证 Agent Client，不能把它当成
任意 MCP Server 的通用 SDK。

### 5.2 普通用户配置

每个 Agent 进程只配置两个值，配置值和 Secret 相互独立：

| 完整变量名 | 默认值 | 必需 | 说明 |
| --- | --- | --- | --- |
| `MEMORY_MCP_URL` | 无 | 是 | 完整 `/mcp` URL |
| `MEMORY_MCP_TOKEN` | 无 | 是 | 必须存在于 Server Token 映射；Secret |

Agent Token 只在 HTTP Authorization 边界解封。不要把它放入 CLI 参数、模型上下文
或 settings 日志。若需要同一用户跨 Agent 共享记忆，服务端为两枚不同 Token 配置
相同 tenant/subject identity；Agent 配置本身不包含 owner。

### 5.3 代码默认值

| 项目 | 默认值 | 用户是否需要配置 |
| --- | --- | --- |
| `profile_id` | `general-work` | 否 |
| HTTP 超时 | 15 秒 | 否 |
| fail-open | `true` | 否 |
| recall 最大条数 | 5 | 否 |
| recall token 预算 | 600 | 否 |
| capture 最大尝试 | 3 | 否 |
| capture 重试间隔 | 0.1 秒 | 否 |
| 进程内回执缓存 | 1000 | 否 |
| 跨进程状态 TTL | 24 小时 | 否 |

高级集成仍可通过显式构造 `MemoryHookSettings` 调整预算。为了兼容已经部署的
环境，`MEMORY_HOOK_MCP_URL` 和 `MEMORY_HOOK_BEARER_TOKEN` 仍作为连接变量别名；
新旧同时存在时 `MEMORY_MCP_URL/TOKEN` 优先。旧的其他 `MEMORY_HOOK_*` 调优变量
暂时保留，但不属于普通用户配置或快速开始合同。

## 6. 测试专用依赖注入

| 变量/对象 | 使用位置 | 说明 |
| --- | --- | --- |
| `MEMORY_MCP_TEST_DATABASE_URL` | PostgreSQL pytest | 必须显式设置并指向名称含 `test` 的可清空 database |
| `InMemoryMemoryRepository` | 单元/transport 测试 | 快速替身，不验证 SQL 或 migration |
| `FakeCandidateExtractor` | Core/transport 测试 | 返回确定候选，不调用网络 |
| `_StructuredModel` | extraction 单元测试 | 验证结构化输出边界 |
| `FixedCandidateBackend` | 自动化 PostgreSQL MCP 闭环 | 测试代码构造并通过 `candidate_extractor` 注入 |
| `examples/hook_runner.py` 的 Agent callable | 手工接线 | 回显处理结果，不是业务大模型 Agent |
| `examples/agents/*.json` | 内置宿主示例 | Codex/Claude Code Hook 注册，不含地址或 Token |

普通 `pytest` 不会自动读取 `.env` 去清空数据库。真实 PostgreSQL 测试必须由操作者
显式设置 `MEMORY_MCP_TEST_DATABASE_URL`，保留人为安全确认。固定候选直接写在
测试用例中，不进入 `.env`，也不能在生产服务启动时选择。

## 7. 固定记忆配置

`GeneralWorkProfile` 当前固定：

| 项目 | 值 |
| --- | --- |
| `profile_id` | `general-work` |
| memory types | `preference`, `stable_context`, `ongoing_item`, `decision` |
| `profile_version` | `general-work-v1` |
| recall priority | preference 40, decision 35, ongoing_item 30, stable_context 20 |
| metadata policy | 全部 `confidential`，不自动过期 |

`InvestmentResearchProfile` 当前固定：

| memory type | 默认敏感级别 | 默认有效期 |
| --- | --- | --- |
| `research_preference` | `confidential` | 无 |
| `research_question` | `confidential` | 365 天 |
| `thesis` | `confidential` | 180 天 |
| `evidence_claim` | `internal` | 90 天 |
| `risk` | `confidential` | 180 天 |
| `catalyst` | `internal` | 90 天 |
| `ongoing_research` | `confidential` | 365 天 |
| `research_decision` | `confidential` | 无 |

投研 Profile 的 `profile_id` 为 `investment-research`，版本为
`investment-research-v1`。Server 启动会同时注册两套内置 Profile，但 MCP 工具和
通用 Hook 的公开默认仍为 `general-work`，不会从正文猜测场景。

普通通用 Agent 仍只配置 URL 与 Token。投研产品应在自己的集成代码中固定
`HookContext(profile_id="investment-research", ...)`；仓库手工测试也可以临时设置
`MEMORY_HOOK_PROFILE_ID=investment-research`。后者是集成调试值，不应让最终用户在
每轮对话中选择或输入 Profile。

`profile_version` 表示“哪一版记忆配置做出了本次抽取与准入决定”，用于审计、回放和
未来规则升级后的差异识别；它不是模型版本，也不是每次请求动态递增的计数器。

`subject` 是可选的精确预过滤条件。通用查询应省略 subject；只有 Agent Host 能
稳定生成与写入一致的规范 subject 时才传入。

### 7.1 元数据含义

- `extraction_confidence`：结构化抽取质量，手工创建或旧数据可为 null；
- `verification_status`：`unverified`、`user_asserted`、`user_confirmed` 或
  `source_verified`，citation 本身不会自动核验；
- `sensitivity_level`：允许保存内容的治理标签，不能绕过敏感阻断；
- `valid_from/valid_until`：普通 list/recall 的读取时有效窗口；到期不删除历史；
- Evidence citation：可选 source type、URI、标题、发布者、发布/获取时间、hash 和
  locator，身份仍只来自 Token。
