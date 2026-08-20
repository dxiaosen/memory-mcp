# Memory MCP 配置参考

运行时配置单一参考。设计原因见[详细总设计](design.md)，操作步骤见[端到端使用](usage.md)。

## 1. 配置边界

| 部署单元 | 发行包 | 模板 | 配置内容 |
| --- | --- | --- | --- |
| Memory MCP Server | `memory-mcp` | `server/.env.example` | 数据库、HTTP、认证、模型、日志 |
| Agent Host | `memory-mcp-agent` | `agent/.env.example` | MCP URL 和该 Host 的 Token |

- Server 与 Agent 是独立进程。Agent 不需要数据库、LangChain、模型 Provider 或 migration。
- `MemoryServerSettings`/`ExtractionSettings` 默认读取项目根目录 `.env`，进程环境变量优先。
- `MemoryHookSettings` 不隐式读取根目录 `.env`，凭据须来自进程环境或显式 env 文件。
- 优先级：显式构造参数 > 进程环境变量 > env 文件 > 代码默认值。生产通过 Secret Manager 或进程 `EnvironmentFile` 注入。
- **禁止**提交或打印 PostgreSQL DSN、Bearer Token 和模型 API Key。
- URI 保留字符须 percent-encode：`@`→`%40`、`:`→`%3A`、`/`→`%2F`、`?`→`%3F`、`#`→`%23`。

## 2. 固定合同与可配置项

| 类别 | 性质 | 说明 |
| --- | --- | --- |
| owner-first、Evidence、准入、revision | 代码固定 | 环境变量不能绕过领域约束 |
| 敏感拦截规则 | 默认固定，可配置注入 | `MEMORY_MCP_SENSITIVE_RULES` 覆盖默认规则集 |
| `general-work`、`investment-research` | 代码固定 | Profile 类型、版本、策略指纹、有效期和关系策略 |
| MCP 工具与 DTO v1 | 代码固定 | 工具参数不接受 owner |
| PostgreSQL schema | migration 管理 | 服务启动前独立升级 |
| 批次 `500`、pending review `30` 天、续批退避 | 代码固定 | 防止无界事务和长期悬挂候选 |
| Agent outbox TTL `24h`、每次 Stop 补送 `1` 条 | 代码固定 | 有界恢复，不形成后台队列 |
| Server 网络、连接池和预算 | 环境可配置 | 有类型与范围校验 |
| 静态 Principal 映射 | 环境可配置 | 可替换为 OAuth/OIDC 认证适配器 |
| 模型 Provider 与参数 | 环境可配置 | 生产始终使用真实模型 |
| Agent 连接 | 每个 Host 独立配置 | 普通用户只填写 URL 和 Token |
| Hook 策略 | 代码默认 | 高级集成可显式构造设置对象 |

## 3. Server 配置

### 3.1 PostgreSQL

| 变量 | 默认值 | 必需 | 说明 |
| --- | --- | --- | --- |
| `MEMORY_MCP_DATABASE_URL` | 无 | 是 | PostgreSQL DSN；Secret |
| `MEMORY_MCP_DATABASE_POOL_MIN_SIZE` | `1` | 否 | 最小连接数，1–50 |
| `MEMORY_MCP_DATABASE_POOL_MAX_SIZE` | `5` | 否 | 最大连接数，1–100 且 ≥ min |
| `MEMORY_MCP_DATABASE_CONNECT_TIMEOUT_SECONDS` | `10` | 否 | 建连/取连接超时，最大 300 秒 |
| `MEMORY_MCP_DATABASE_MIGRATE_ON_STARTUP` | `false` | 否 | 本地可开启；生产推荐独立 migration |

- 生产连接按基础设施要求启用 TLS。
- 破坏性测试只允许名称含 `test` 的专用可清空数据库。
- VPC/VPN 内可直接访问 `http://host:port/mcp`，不要求 Nginx；公网由 LB 终止 HTTPS 转发到受限私网端口。

### 3.2 HTTP 与资源预算

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MEMORY_MCP_HOST` | `127.0.0.1` | 私网监听设 `0.0.0.0`，同时限制安全组 |
| `MEMORY_MCP_PORT` | `8765` | HTTP 端口 |
| `MEMORY_MCP_MCP_PATH` | `/mcp` | Streamable HTTP 路径 |
| `MEMORY_MCP_HEALTH_PATH` | `/health` | 健康检查路径，不能与 MCP path 相同 |
| `MEMORY_MCP_STATELESS_HTTP` | `true` | 无状态 HTTP session |
| `MEMORY_MCP_MAX_CAPTURE_CHARACTERS` | `100000` | 单轮最大字符数，1,000–1,000,000 |
| `MEMORY_MCP_RECALL_MAX_ITEMS` | `10` | 召回条数硬上限，1–10 |
| `MEMORY_MCP_RECALL_MAX_TOKEN_BUDGET` | `1200` | 渲染预算硬上限，64–8,000 |
| `MEMORY_MCP_RECALL_CANDIDATE_LIMIT` | `500` | Recall 读取候选硬上限，1–10,000 |
| `MEMORY_MCP_MAINTENANCE_INTERVAL_SECONDS` | `300` | 服务端维护周期，0–86,400 秒；`0` 禁用 |
| `MEMORY_MCP_CAPTURE_ENQUEUE_ENABLED` | `true` | `true`：capture 只入队（毫秒级返回 `pending`），worker 异步抽取；`false`：回退同步抽取 |
| `MEMORY_MCP_CAPTURE_REPROCESS_INTERVAL_SECONDS` | `5` | worker 轮询 pending capture 的间隔，0–3,600 秒；`0` 关闭 |

- 维护 runner 与 Server 共用进程和连接池，同步 DB 调用在线程中执行，不阻塞事件循环。
- 每批最多 500 revision/review；连续 `has_more` 超 8 次后插入 1 秒退避。
- `maintenance.state`：`disabled/starting/ok/degraded`。普通部署保持默认，Agent 不读取该变量。
- capture-reprocess worker 同构：每批最多 20 条 pending capture；连续 `has_more` 超 16 次后插入 1 秒退避；`capture_reprocess.state` 同 `maintenance`。

### 敏感拦截规则

`MEMORY_MCP_SENSITIVE_RULES` 是 JSON 数组，每项 `{"category": str, "pattern": str}`，覆盖默认规则集（credential、account_secret、real_holding、transaction_instruction）：

```json
[{"category": "project_codename", "pattern": "阿波罗"},
 {"category": "internal_contact", "pattern": "(?:手机|电话)\\s*[:：]?\\s*\\d{11}"}]
```

- 配置后**完全替换**默认规则集，不合并；空数组视为未配置。
- 非法正则启动阶段安全失败。
- 投研场景 `transaction_instruction` 可能误伤研究偏好文本，部署可按需调整。语义见[日志规范](logging.md)。

### 3.3 静态认证

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MEMORY_MCP_AUTH_ISSUER_URL` | `http://localhost/memory-mcp-auth` | MCP auth metadata issuer |
| `MEMORY_MCP_RESOURCE_SERVER_URL` | 根据请求推导 | MCP resource URL |
| `MEMORY_MCP_AUTH_TOKENS` | `{}` | Token 到 Principal 的 JSON 映射；为空拒绝启动；Secret |

`MEMORY_MCP_AUTH_TOKENS` 每个 key 是 ≥32 字符高熵 Token，value 包含：

| 字段 | 必需 | 含义 |
| --- | --- | --- |
| `tenant_id` | 否 | 默认 `default` |
| `subject_id` | 是 | 授权系统中的不可变用户 ID |
| `default_profile_id` | 否 | 默认 `general-work`，启动时校验已注册 |
| `scopes` | 否 | 默认 read/write/review，可收窄 |
| `team_ids` | 否 | 所属团队 ID 列表；召回时合并个人与团队记忆 |

```json
{"<random-token>": {
  "tenant_id": "tenant-001", "subject_id": "subject-001",
  "default_profile_id": "investment-research", "team_ids": ["research-dept"],
  "scopes": ["memory:read", "memory:write", "memory:review"]}}
```

- 服务端固定派生 `owner_key = tenant_id:subject_id`，不可通过 MCP 参数覆盖。
- Token SHA-256 摘要仅作日志 reference，不参与授权。
- 当前适配器不负责动态签发、过期、轮换或 OAuth federation。
- Profile 缺省时由 Token 的 `default_profile_id` 选择。

### 多层记忆

- Token 配置 `team_ids` 后，召回时同时匹配个人记忆和团队公共记忆。
- 团队 owner key 由 `tenant_id:team:team_id` 派生，与个人 owner key 不冲突。
- 个人记忆写个人 owner；团队公共记忆通过 review 确认时显式提升（`promote_to_team`）写入团队 owner，capture 热路径不改变。

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

- 候选和关系抽取共享一个 ChatModel，使用独立 prompt 和严格 schema。
- 配置缺失时启动失败，不降级为测试替身。
- 模型只参与 AfterRun 候选/关系抽取；BeforeRun 召回使用 `pg_jieba` 全文检索 + 可选向量候选及确定性应用层排序。

### 3.5 向量召回（可选）

| 变量 | 默认值 | 必需 | 说明 |
| --- | --- | --- | --- |
| `MEMORY_MCP_EMBEDDING_MODEL` | `text-embedding-v3` | 否 | embedding 模型 ID |
| `MEMORY_MCP_EMBEDDING_API_KEY` | 无 | 否 | embedding provider 凭据；Secret；未配置则跳过向量召回路 |
| `MEMORY_MCP_EMBEDDING_BASE_URL` | Provider 默认 | 否 | 官方或兼容 embeddings 地址 |
| `MEMORY_MCP_EMBEDDING_DIMENSIONS` | `1024` | 否 | 输出向量维度，需与 pgvector 列一致 |
| `MEMORY_MCP_EMBEDDING_TIMEOUT_SECONDS` | `30` | 否 | 单次调用超时 |
| `MEMORY_MCP_EMBEDDING_MAX_RETRIES` | `2` | 否 | Provider 重试，0–10 |

- 配置 `API_KEY` 后，capture 写入期为 revision 计算 embedding 存入 pgvector 列；recall 增加向量余弦候选路。
- 未配置或计算失败时降级为词法+近期两路召回，不影响主链路。

### 3.6 团队公共记忆自动提取（可选）

| 变量 | 默认值 | 必需 | 说明 |
| --- | --- | --- | --- |
| `MEMORY_MCP_TEAM_EXTRACTION_INTERVAL_SECONDS` | `3600` | 否 | 团队提取周期，0 关闭；范围 0–86400 |
| `MEMORY_MCP_TEAM_EXTRACTION_SIMILARITY_THRESHOLD` | `0.70` | 否 | embedding 聚类相似度阈值；投研共性提取场景语义近似但措辞不同是常态 |
| `MEMORY_MCP_TEAM_EXTRACTION_MIN_CLUSTER_SIZE` | `2` | 否 | 最小簇大小 |

- 服务端周期性扫描团队成员个人记忆，用 embedding 相似度聚类提取公共知识候选，写入团队 pending review。
- 团队成员从 `MEMORY_MCP_AUTH_TOKENS` 的 `team_ids` 派生；同 tenant 下配相同 team_id 的成员构成团队。
- 不自动确认——候选需成员人工确认后沉淀为团队公共记忆。
- 模型不可用不影响已有记忆的维护或召回。
- 候选级幂等覆盖 pending 与 confirmed：同 subject+type 已有团队 pending 或 confirmed 记忆时不重复创建，避免已确认共识被重复提交为 pending。

### 3.7 日志

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MEMORY_MCP_LOG_LEVEL` | `INFO` | `DEBUG/INFO/WARNING/ERROR` |
| `MEMORY_MCP_LOG_CONTENT` | `false` | 是否记录清洗后的业务内容 |
| `MEMORY_MCP_LOG_FILE` | `.memory-mcp/logs/memory-mcp.log` | 空字符串只写 stderr |
| `MEMORY_MCP_LOG_MAX_BYTES` | `10485760` | 单文件轮转阈值 |
| `MEMORY_MCP_LOG_BACKUP_COUNT` | `5` | 轮转文件数 |

- Bearer Token、DSN、API Key、Provider 异常正文和敏感规则拦截原文始终禁止记录。
- 完整合同见[日志规范](logging.md)。

## 4. Agent Host 配置

| 变量 | 默认值 | 必需 | 说明 |
| --- | --- | --- | --- |
| `MEMORY_MCP_URL` | 无 | 是 | 完整 `/mcp` URL |
| `MEMORY_MCP_TOKEN` | 无 | 是 | Server 已映射的 Bearer Token；Secret |
| `MEMORY_HOOK_RECALL_TIMEOUT_SECONDS` | `15` | 否 | recall HTTP 超时；需在用户请求开始前返回 |

- Agent 默认不发送 `profile_id`，由 Token 的 `default_profile_id` 决定策略。
- Phase 1（模型自主调用 capture）后，capture 由模型自主调用 `capture_completed_turn` MCP 工具，不再经 Agent 客户端，故 capture 超时/重试设置已移除。recall fail-open 默认开启，召回最多 5 条/600 token。
- `MEMORY_HOOK_PROFILE_ID` 可作进程级高级覆盖，不进入普通 Agent 模板。
- Agent Token 只在 HTTP Authorization 边界解封，不能放进 CLI 参数、模型上下文或日志。
- 同一用户跨 Agent 发放不同 Token，映射到相同 tenant/subject。
- command Hook 本地状态目录（`cwd/.memory-mcp/hooks`，`0700/0600`）仅保留 24h TTL 清理残留旧版本文件，不再写入新状态。
- 不需要 Redis、消息队列或常驻 Agent daemon。

## 5. 最小模板

```bash
cp server/.env.example .env && chmod 600 .env
cp agent/.env.example examples/agent.env && chmod 600 examples/agent.env
```

- `server/.env.example` 只含生产 Server 配置，`agent/.env.example` 只含 URL 和 Token。
- 测试数据库、候选 fixture 和多身份矩阵由测试代码显式提供，不进入生产模板。
