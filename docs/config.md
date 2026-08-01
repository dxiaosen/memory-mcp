# Memory MCP 配置参考

本文是运行时配置的单一参考。设计原因见[详细总设计](design.md)，操作步骤见
[端到端使用](usage.md)。

## 1. 配置边界

| 部署单元 | 发行包 | 模板 | 配置内容 |
| --- | --- | --- | --- |
| Memory MCP Server | `memory-mcp` | `server/.env.example` | 数据库、HTTP、认证、模型和日志 |
| Agent Host | `memory-mcp-agent` | `agent/.env.example` | MCP URL 和该 Host 的 Token |

Server 与 Agent 是两个独立进程。Agent 不需要数据库、LangChain、模型 Provider 或
migration 命令；Server 不读取 Agent Hook 配置。仓库开发时使用同一个 `.venv` 只是
为了测试两个 workspace package。

`MemoryServerSettings` 和 `ExtractionSettings` 本地默认读取项目根目录 `.env`，
进程环境变量优先。`MemoryHookSettings` 不隐式读取项目根目录 `.env`，Agent 凭据
必须来自进程环境或由示例命令显式指定的 env 文件。

配置优先级为：显式构造参数、进程环境变量、env 文件、代码默认值。生产环境推荐
通过 Secret Manager、systemd `EnvironmentFile` 或编排平台注入 Secret。不得提交
或打印 PostgreSQL DSN、Bearer Token 和模型 API Key。

PostgreSQL URI 中的保留字符必须 percent-encode，例如 `@` 写为 `%40`、`:` 写为
`%3A`、`/` 写为 `%2F`、`?` 写为 `%3F`、`#` 写为 `%23`。

## 2. 固定合同与可配置项

| 类别 | 性质 | 说明 |
| --- | --- | --- |
| owner-first、Evidence、准入、revision 和敏感拦截 | 代码固定 | 环境变量不能绕过领域约束 |
| `general-work`、`investment-research` | 代码固定 | Profile 类型、版本、有效期和关系策略 |
| MCP 工具与 DTO v1 | 代码固定 | 工具参数不接受 owner |
| PostgreSQL schema | migration 管理 | 服务启动前独立升级 |
| Server 网络、连接池和预算 | 环境可配置 | 有类型与范围校验 |
| 静态 Principal 映射 | 环境可配置 | 可替换为 OAuth/OIDC 认证适配器 |
| 模型 Provider 与参数 | 环境可配置 | 生产始终使用真实模型 |
| Agent 连接 | 每个 Host 独立配置 | 普通用户只填写 URL 和 Token |
| Hook 策略 | 代码默认 | 高级集成可显式构造设置对象 |

Profile、元数据、自动关系和 `profile_version` 的完整语义统一见
[详细总设计](design.md)；测试替身和专用测试数据库见[测试与验收](testing.md)。

## 3. Server 配置

### 3.1 PostgreSQL

| 变量 | 默认值 | 必需 | 说明 |
| --- | --- | --- | --- |
| `MEMORY_MCP_DATABASE_URL` | 无 | 是 | PostgreSQL DSN；Secret |
| `MEMORY_MCP_DATABASE_POOL_MIN_SIZE` | `1` | 否 | 最小连接数，1–50 |
| `MEMORY_MCP_DATABASE_POOL_MAX_SIZE` | `5` | 否 | 最大连接数，1–100 且不小于 min |
| `MEMORY_MCP_DATABASE_CONNECT_TIMEOUT_SECONDS` | `10` | 否 | 建连/取连接超时，最大 300 秒 |
| `MEMORY_MCP_DATABASE_MIGRATE_ON_STARTUP` | `false` | 否 | 本地可开启；生产推荐独立 migration |

生产连接按基础设施要求启用 TLS。破坏性测试只允许使用名称包含 `test` 的专用、
可清空数据库。

### 3.2 HTTP 与资源预算

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MEMORY_MCP_HOST` | `127.0.0.1` | 私网监听可设为 `0.0.0.0`，同时限制安全组 |
| `MEMORY_MCP_PORT` | `8765` | HTTP 端口 |
| `MEMORY_MCP_MCP_PATH` | `/mcp` | Streamable HTTP 路径 |
| `MEMORY_MCP_HEALTH_PATH` | `/health` | 健康检查路径，不能与 MCP path 相同 |
| `MEMORY_MCP_STATELESS_HTTP` | `true` | 无状态 HTTP session |
| `MEMORY_MCP_MAX_CAPTURE_CHARACTERS` | `100000` | 单轮最大字符数，1,000–1,000,000 |
| `MEMORY_MCP_RECALL_MAX_ITEMS` | `10` | 召回条数硬上限，1–10 |
| `MEMORY_MCP_RECALL_MAX_TOKEN_BUDGET` | `1200` | 渲染预算硬上限，64–8,000 |

同一 VPC/VPN 内可直接访问 `http://host:port/mcp`，不要求 Nginx。公网应由负载均衡
入口终止 HTTPS，再转发到受限私网端口。

### 3.3 静态认证

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MEMORY_MCP_AUTH_ISSUER_URL` | `http://localhost/memory-mcp-auth` | MCP auth metadata issuer |
| `MEMORY_MCP_RESOURCE_SERVER_URL` | 根据请求推导 | MCP resource URL |
| `MEMORY_MCP_AUTH_TOKENS` | `{}` | Token 到可信 Principal 的 JSON 映射；为空拒绝启动；Secret |

`MEMORY_MCP_AUTH_TOKENS` 的每个 key 是至少 32 字符的独立高熵 Token，value 包含：

| 字段 | 必需 | 含义 |
| --- | --- | --- |
| `tenant_id` | 否 | 默认 `default` |
| `subject_id` | 是 | 授权系统中的不可变用户 ID |
| `scopes` | 否 | 默认 read/write/review，可收窄 |

```json
{
  "<random-token>": {
    "tenant_id": "tenant-001",
    "subject_id": "subject-001",
    "scopes": ["memory:read", "memory:write", "memory:review"]
  }
}
```

服务端固定派生 `owner_key = tenant_id:subject_id`。同一用户的多枚 Token 可映射到
同一 owner，不同 subject 自然隔离；调用方不能通过 MCP 参数覆盖 owner。Token 的
SHA-256 摘要仅作为日志 client reference，不参与授权。当前适配器不负责动态签发、
过期、轮换编排或 OAuth federation；生产需要这些能力时应替换认证适配器。

### 3.4 模型与候选生成

| 变量 | 默认值 | 必需 | 说明 |
| --- | --- | --- | --- |
| `MEMORY_MCP_MODEL_PROVIDER` | `deepseek` | 否 | `deepseek` 或 `openai` |
| `MEMORY_MCP_MODEL_NAME` | 无 | 是 | 模型 ID |
| `MEMORY_MCP_MODEL_API_KEY` | 无 | 是 | 模型凭据；Secret |
| `MEMORY_MCP_MODEL_BASE_URL` | Provider 默认 | 否 | 官方或兼容 Chat Completions 地址 |
| `MEMORY_MCP_MODEL_TEMPERATURE` | `0` | 否 | 0–2 |
| `MEMORY_MCP_MODEL_TIMEOUT_SECONDS` | `60` | 否 | 单次调用超时，最大 300 秒 |
| `MEMORY_MCP_MODEL_MAX_RETRIES` | `2` | 否 | Provider 重试，0–10 |

候选和关系抽取共享一个 ChatModel 配置，但使用独立的 prompt 和严格 schema。配置
缺失时服务启动失败，不会降级为测试替身。模型不接收 owner、Token 或 DSN，输出
仍要经过 Evidence、Profile 类型、敏感边界和准入规则校验。DeepSeek adapter 会
关闭与强制 schema tool choice 不兼容的 thinking 模式。

### 3.5 日志

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MEMORY_MCP_LOG_LEVEL` | `INFO` | `DEBUG/INFO/WARNING/ERROR` |
| `MEMORY_MCP_LOG_CONTENT` | `false` | 是否记录清洗后的业务内容 |
| `MEMORY_MCP_LOG_FILE` | `.memory-mcp/logs/memory-mcp.log` | 空字符串表示只写 stderr/journal |
| `MEMORY_MCP_LOG_MAX_BYTES` | `10485760` | 单文件轮转阈值 |
| `MEMORY_MCP_LOG_BACKUP_COUNT` | `5` | 轮转文件数 |

默认日志只记录稳定引用、阶段、状态、数量、错误码和耗时。内容日志只应在受控联调
期间短期开启；Bearer Token、DSN、API Key、Provider 异常正文和敏感规则拦截原文
始终禁止记录。完整合同见[日志规范](logging.md)。

## 4. Agent Host 配置

普通用户只配置：

| 变量 | 默认值 | 必需 | 说明 |
| --- | --- | --- | --- |
| `MEMORY_MCP_URL` | 无 | 是 | 完整 `/mcp` URL |
| `MEMORY_MCP_TOKEN` | 无 | 是 | Server 已映射的 Bearer Token；Secret |

`profile_id` 默认 `general-work`，HTTP 超时默认 15 秒，fail-open 默认开启，召回最多
5 条/600 token，capture 最多尝试 3 次。投研产品应在集成代码中固定
`investment-research`，不让终端用户按轮选择 Profile。需要调整预算或重试时，由
宿主显式构造 `MemoryHookSettings`；这些不是普通部署必填项。

Agent Token 只在 HTTP Authorization 边界解封，不能放进 CLI 参数、模型上下文或
settings 日志。同一用户跨 Agent 共享记忆时，为不同 Host 发放不同 Token，并在
Server 端映射到相同 tenant/subject。

## 5. 最小模板

本地开发：

```bash
cp server/.env.example .env
chmod 600 .env
cp agent/.env.example examples/agent.env
chmod 600 examples/agent.env
```

`server/.env.example` 只包含生产 Server 配置，`agent/.env.example` 只包含 URL 和
Token。测试数据库、候选 fixture 和多身份矩阵由测试或验收代码显式提供，不进入
生产模板。
