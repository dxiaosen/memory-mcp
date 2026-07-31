# Memory MCP 配置参考

本文是运行时配置的单一参考；`.env.example` 是可复制模板，设计原因见
[整体设计](design.md)，操作步骤见[端到端使用](usage.md)。

## 1. 加载、安全与优先级

服务端、模型适配器和 Hook Client 都使用 Pydantic Settings：

1. 构造参数优先级最高，主要供测试和显式依赖注入使用；
2. 进程环境变量覆盖 `.env`；
3. 项目根目录 `.env` 提供本地默认值；
4. 未配置项使用代码默认值，必需项在启动或实际基础设施边界前失败。

`.env` 已被 Git 忽略，但仍应限制为当前用户可读：

```bash
cp .env.example .env
chmod 600 .env
```

不得把真实 DSN、Bearer Token、模型 API Key 或包含真实用户原文的固定候选提交
到仓库、命令行参数、测试快照或日志。

PostgreSQL URI 的用户名或密码若包含保留字符必须 percent-encode，例如 `@` 写为
`%40`，`:` 写为 `%3A`，`/` 写为 `%2F`，`?` 写为 `%3F`，`#` 写为 `%23`，
`%` 写为 `%25`。否则 URI 的 host、port 或 database 会被错误拆分。

## 2. 哪些是固定的，哪些可配置

| 类别 | 当前性质 | 说明 |
| --- | --- | --- |
| Core 领域规则与四类准入 | 代码固定 | owner-first、Evidence、revision、pending、敏感拦截和幂等不能靠环境变量绕过 |
| `GeneralWorkPolicy` | 代码固定 | 场景名、四种 memory type、捕获说明、policy version 和召回优先级 |
| MCP 工具与 DTO v1 | 代码固定 | 七个工具；capture contract version 为 `1` |
| PostgreSQL schema/migration | 版本固定 | 通过独立 migration 命令升级，不在运行时动态拼表 |
| Server 地址、连接池、预算 | 环境可配置 | 有边界校验和安全默认值 |
| 身份映射 | 原型环境配置 | 仅用于当前演示；生产应替换正式授权服务 |
| extractor backend | 环境可切换 | `fixed` 用于确定性演示/测试；`openai-compatible` 调真实模型 |
| Hook profile | 每个 Agent 独立配置 | URL、Token、超时、fail-open、召回预算和重试 |
| InMemory Repository | 仅测试 | 不属于部署运行路径 |
| Fake extractor/model | 仅自动化测试 | 不访问模型网络 |
| `.env.example` 的 Token/DSN/模型值 | 占位或测试样例 | 必须替换，不能作为生产凭据 |

`fixed` 并不等于“整个系统是 mock”：它只替代候选生成，MCP transport、鉴权、
Core、准入、PostgreSQL、Hook 和跨 Agent 共享仍是真实链路。

## 3. Server 配置

变量均以 `MEMORY_MCP_` 开头。

### 3.1 PostgreSQL

| 变量 | 默认值 | 必需 | 说明 |
| --- | --- | --- | --- |
| `DATABASE_URL` | 无 | 是 | PostgreSQL DSN；Secret |
| `DATABASE_POOL_MIN_SIZE` | `1` | 否 | 连接池最小连接数，范围 1–50 |
| `DATABASE_POOL_MAX_SIZE` | `5` | 否 | 最大连接数，范围 1–100，且不小于 min |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | `10` | 否 | 建连/取连接超时，最大 300 秒 |
| `DATABASE_MIGRATE_ON_STARTUP` | `false` | 否 | 本地可临时开启；部署固定采用独立 migration 步骤 |

生产或公网数据库链路应启用实例 SSL 并使用 `sslmode=require`。测试数据库必须是
独立可清空实例或 database，名称包含 `test`；自动化会执行级联 truncate。

### 3.2 HTTP、契约与预算

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | 本机默认；ECS 私网监听常用 `0.0.0.0` 并限制安全组 |
| `PORT` | `8765` | 监听端口 |
| `MCP_PATH` | `/mcp` | Streamable HTTP 路径 |
| `HEALTH_PATH` | `/health` | 健康检查路径，不能与 MCP path 相同 |
| `STATELESS_HTTP` | `true` | 当前服务采用无状态 HTTP session |
| `MAX_CAPTURE_CHARACTERS` | `100000` | 一次完成轮次的最大字符数，范围 1,000–1,000,000 |
| `RECALL_MAX_ITEMS` | `10` | 服务端召回条数硬上限，范围 1–10 |
| `RECALL_MAX_TOKEN_BUDGET` | `1200` | 服务端渲染预算硬上限，范围 64–8,000 |

可信私网可以直接访问 `http://host:port/mcp`，不需要 Nginx。公网由 ALB/CLB
终止 HTTPS，再转发到受限 ECS 端口。

### 3.3 原型认证

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `AUTH_ISSUER_URL` | `http://localhost/demo-auth` | MCP auth metadata 的 issuer |
| `RESOURCE_SERVER_URL` | 未设置 | 未设置时由服务推导 MCP resource URL |
| `DEMO_TOKENS_JSON` | `{}` | Token 到可信 Principal 的映射；为空拒绝启动；Secret |

每个 JSON value 的字段：

| 字段 | 必需 | 含义 |
| --- | --- | --- |
| `owner_key` | 是 | Repository 的最终隔离键 |
| `tenant_id` | 否 | 默认 `demo` |
| `subject_id` | 是 | 授权系统中的最终用户身份 |
| `client_id` | 是 | 调用客户端身份 |
| `agent_id` | 否 | 可选 Agent 审计身份 |
| `scopes` | 否 | 默认 read/write/review，可收窄 |

同一 `(tenant_id, subject_id)` 必须唯一映射到一个 owner；一个 owner 也不能别名到
多个 subject。用户 A 的 Agent A/B 可共享 owner，用户 B 必须使用不同 owner。
客户端不能通过 MCP 参数提交或覆盖 owner。

### 3.4 抽取与日志

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `EXTRACTOR_BACKEND` | `fixed` | `fixed` 或 `openai-compatible` |
| `FIXED_CANDIDATES_JSON` | `[]` | 严格候选数组；仅精确命中 `source_expression` 才返回 |
| `LOG_LEVEL` | `INFO` | `DEBUG/INFO/WARNING/ERROR` |
| `LOG_FILE` | `.memory-mcp/logs/memory-mcp.log` | 设为空可只输出 stderr |
| `LOG_MAX_BYTES` | `10485760` | 单日志文件轮转阈值 |
| `LOG_BACKUP_COUNT` | `5` | 保留的轮转文件数 |

日志只记录稳定哈希引用、工具名、状态、数量和耗时；不记录对话、记忆、Token、
DSN、API Key 或 backend 异常正文。

## 4. 真实模型配置

模型变量没有 `MEMORY_MCP_` 前缀，只有
`MEMORY_MCP_EXTRACTOR_BACKEND=openai-compatible` 时才参与运行。

| 变量 | 默认值 | 必需 | 说明 |
| --- | --- | --- | --- |
| `CHAT_MODEL_PROVIDER` | `deepseek` | 否 | `deepseek` 或 `openai` |
| `CHAT_MODEL_NAME` | 无 | 是 | provider 可用的模型 ID |
| `CHAT_MODEL_API_KEY` | 无 | 是 | Secret |
| `CHAT_MODEL_BASE_URL` | provider 默认 | 否 | 官方或兼容 Chat Completions 地址 |
| `CHAT_MODEL_TEMPERATURE` | `0` | 否 | 范围 0–2 |
| `CHAT_MODEL_TIMEOUT_SECONDS` | `60` | 否 | 单次模型超时 |
| `CHAT_MODEL_MAX_RETRIES` | `2` | 否 | LangChain/provider 层重试，范围 0–10 |

真实模型只接收脱敏后的场景、subject hint、时间和本轮正文，不接收 owner、Token、
DSN。模型输出必须通过 `CandidateBatch`、原文证据、场景类型、敏感边界和准入的
二次校验。

DeepSeek V4 默认开启 thinking，但 LangChain 的强制 schema tool choice 与该模式
不兼容。当前 DeepSeek extraction adapter 固定关闭 thinking；这是抽取适配策略，
不是用户可调推理开关。OpenAI provider 不应用该参数。

## 5. Hook profile 配置

`MemoryHookSettings.from_profile("agent-a")` 将 profile 转为
`MEMORY_AGENT_A_`；`user-b-agent-b` 对应 `MEMORY_USER_B_AGENT_B_`。

| 后缀 | 默认值 | 必需 | 说明 |
| --- | --- | --- | --- |
| `MCP_URL` | 无 | 是 | 完整 `/mcp` URL |
| `BEARER_TOKEN` | 无 | 是 | 必须存在于 Server 映射；Secret |
| `SCENARIO` | `general-work` | 否 | 已注册场景 |
| `TIMEOUT_SECONDS` | `15` | 否 | Hook HTTP 超时 |
| `FAIL_OPEN` | `true` | 否 | 记忆故障是否允许 Agent 主任务继续 |
| `RECALL_MAX_ITEMS` | `5` | 否 | 客户端请求上限，仍受 Server 上限约束 |
| `RECALL_TOKEN_BUDGET` | `600` | 否 | 客户端渲染预算 |
| `CAPTURE_MAX_ATTEMPTS` | `3` | 否 | AfterRun 有界重试次数 |
| `CAPTURE_RETRY_DELAY_SECONDS` | `0.1` | 否 | 重试间隔 |
| `RUN_CACHE_MAX_ENTRIES` | `1000` | 否 | 单 Bridge 的完成 receipt 缓存上限 |

profile 的 URL 与 Token 必须成对独立配置。Bearer Token 只在 HTTP Authorization
边界解封，不应作为 CLI 参数或打印 settings。

## 6. 测试专用配置

| 变量/对象 | 使用位置 | 说明 |
| --- | --- | --- |
| `MEMORY_MCP_TEST_DATABASE_URL` | PostgreSQL pytest | 必须指向名称含 `test` 的可清空数据库 |
| `InMemoryMemoryRepository` | 单元/MCP transport 测试 | 快速替身，不验证 SQL/migration |
| `FakeCandidateExtractor` | Core/transport 测试 | 返回确定候选，不调用网络 |
| `_StructuredModel` | extraction 单元测试 | 验证 LangChain schema 边界 |
| `fixed` backend | 自动化和手工闭环 | 真实服务链路中的确定性 extractor |
| `examples/*` 的 demo Agent callable | 手工接线示例 | 只回显处理结果，不是业务大模型 Agent |

不要让普通 `uv run pytest` 自动读取 `.env` 并清空数据库；外部 E2E 必须显式把
专用 DSN 映射到 `MEMORY_MCP_TEST_DATABASE_URL`，从而保留人为安全确认。

## 7. 固定场景值

`GeneralWorkPolicy` 当前固定：

| 项目 | 值 |
| --- | --- |
| scenario | `general-work` |
| memory types | `preference`, `stable_context`, `ongoing_item`, `decision` |
| policy version | `general-work-v1` |
| recall priority | preference 40, decision 35, ongoing_item 30, stable_context 20 |

`subject` 是可选的精确预过滤条件。真实模型可能把 subject 归纳为项目名；若调用方
传入不同 subject，即使 query 相关也会先被过滤。通用查询应省略 subject，只有
Host 能稳定生成与写入一致的规范 subject 时才传入。
