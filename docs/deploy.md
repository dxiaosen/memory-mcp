# Memory MCP 部署

P0 部署形态：Memory MCP 直接运行在 Linux ECS 上，VPC 私网连接托管 PostgreSQL，不安装 Docker 或 Nginx。

## 1. 目标拓扑

```text
VPC/VPN Agent ── HTTP + Authorization ───────┐
                                             │
公网 Agent ── HTTPS ── 可选 ALB/CLB ── HTTP ─┤
                                             ▼
                        Memory MCP <ECS_PRIVATE_IP>:8765  (systemd)
                                             │
                                      VPC 私网连接
                                             ▼
                                      RDS PostgreSQL
```

- `deploy/` 目录只存放运维制品（两个 systemd unit），不是应用代码包或数据目录。

## 2. 前置条件

- ECS 与 PostgreSQL 位于同地域、同 VPC，或已建立可控私网路由。
- PostgreSQL 创建独立数据库和最小权限应用账号；migration 账号需 `CREATE EXTENSION pg_trgm` 与 `CREATE EXTENSION vector`（pgvector）权限（`0001_memory_schema.sql` 要求）。
- 公网接入时，ALB/CLB 已配置域名和有效 TLS 证书。
- Linux 已安装 `uv`；项目部署到 `/opt/memory-mcp`；时间同步正常。

## 3. 网络与安全组

| 端口 | 来源 | 用途 |
| --- | --- | --- |
| `22` | 管理员固定 IP | 运维 SSH |
| `8765` | 可信 VPC/VPN Agent 网段；可选 ALB/CLB 安全组 | MCP 服务直连 |

- ALB/CLB 公网 `443` 配置在负载均衡器安全组，不配置在 ECS 安全组。
- ECS `8765` 不得开放给 `0.0.0.0/0`。PostgreSQL 只允许 ECS 安全组或私网地址访问。
- 公网入口必须在负载均衡器终止 HTTPS；不得在公网使用带 Bearer Token 的明文 HTTP。

## 4. 安装应用

| 步骤 | 命令 | 验证 |
| --- | --- | --- |
| 创建用户和目录 | `sudo useradd --system --create-home --shell /usr/sbin/nologin memory-mcp`<br>`sudo mkdir -p /opt/memory-mcp /etc/memory-mcp /var/log/memory-mcp`<br>`sudo chown -R memory-mcp:memory-mcp /opt/memory-mcp /var/log/memory-mcp`<br>`sudo chown root:memory-mcp /etc/memory-mcp && sudo chmod 750 /etc/memory-mcp` | `id memory-mcp` |
| 同步代码 | （将代码同步到 `/opt/memory-mcp`） | `ls /opt/memory-mcp/pyproject.toml` |
| 安装依赖 | `cd /opt/memory-mcp && sudo -u memory-mcp uv sync --frozen --no-dev --package memory-mcp` | `.venv/bin/memory-mcp --help` |

- Python 3.14，`uv` 按项目声明准备隔离环境。
- 必须指定 `--package memory-mcp`，Server 不应安装 Agent 发行包。

## 5. 运行配置

| 步骤 | 命令 | 验证 |
| --- | --- | --- |
| 创建配置 | `sudo cp server/.env.example /etc/memory-mcp/memory-mcp.env`（编辑后只保留实际需要的值） | 见下方示例 |
| 设置权限 | `sudo chown root:memory-mcp /etc/memory-mcp/memory-mcp.env`<br>`sudo chmod 640 /etc/memory-mcp/memory-mcp.env` | `ls -l /etc/memory-mcp/memory-mcp.env` |

```dotenv
MEMORY_MCP_DATABASE_URL='postgresql://memory_app:URL_ENCODED_PASSWORD@RDS_PRIVATE_HOST:5432/memory_mcp?sslmode=require'
MEMORY_MCP_DATABASE_POOL_MIN_SIZE=1
MEMORY_MCP_DATABASE_POOL_MAX_SIZE=5
MEMORY_MCP_DATABASE_CONNECT_TIMEOUT_SECONDS=10
MEMORY_MCP_DATABASE_MIGRATE_ON_STARTUP=false

MEMORY_MCP_HOST=0.0.0.0
MEMORY_MCP_PORT=8765
MEMORY_MCP_MCP_PATH=/mcp
MEMORY_MCP_HEALTH_PATH=/health
MEMORY_MCP_RECALL_CANDIDATE_LIMIT=500
MEMORY_MCP_MAINTENANCE_INTERVAL_SECONDS=300

MEMORY_MCP_AUTH_ISSUER_URL=https://memory.example.com/auth
MEMORY_MCP_RESOURCE_SERVER_URL=https://memory.example.com/mcp
MEMORY_MCP_AUTH_TOKENS='{"REPLACE_WITH_RANDOM_TOKEN_AT_LEAST_32_CHARACTERS":{"tenant_id":"tenant-001","subject_id":"subject-001","default_profile_id":"investment-research","team_ids":["research-dept"],"scopes":["memory:read","memory:write","memory:review"]}}'

MEMORY_MCP_MODEL_PROVIDER=openai
MEMORY_MCP_MODEL_NAME=REPLACE_WITH_MODEL_ID
MEMORY_MCP_MODEL_API_KEY=REPLACE_WITH_MODEL_SECRET
MEMORY_MCP_MODEL_BASE_URL=https://api.openai.com/v1
MEMORY_MCP_MODEL_TEMPERATURE=0
MEMORY_MCP_MODEL_TIMEOUT_SECONDS=60
MEMORY_MCP_MODEL_MAX_RETRIES=2

MEMORY_MCP_LOG_LEVEL=INFO
MEMORY_MCP_LOG_CONTENT=false
MEMORY_MCP_LOG_FILE=/var/log/memory-mcp/memory-mcp.log
```

- 数据库密码 `@`、`:`、`/`、`#` 等须 URL 编码。
- 每枚静态 Token ≥32 字符，不同 Agent 使用不同随机值。
- `LOG_CONTENT=false` 保持生产环境；只在受控联调窗口临时开启。

## 6. 数据库迁移

| 步骤 | 命令 | 验证 |
| --- | --- | --- |
| 安装迁移 unit | `sudo cp deploy/systemd/memory-mcp-migrate.service /etc/systemd/system/`<br>`sudo systemctl daemon-reload` | `systemctl cat memory-mcp-migrate.service` |
| 执行迁移 | `sudo systemctl start memory-mcp-migrate.service` | `sudo systemctl status memory-mcp-migrate.service` |
| 验证 schema | `sudo -u memory-mcp /bin/bash -c 'set -a; source /etc/memory-mcp/memory-mcp.env; set +a; exec /opt/memory-mcp/.venv/bin/memory-mcp-db health'` | `Memory PostgreSQL is healthy` |

- 迁移记录 checksum，已执行的 migration 文件被修改后续迁移会拒绝执行。
- 重复执行 migration unit 应报告 schema 已是最新状态。

## 7. 启动 systemd 服务

| 步骤 | 命令 | 验证 |
| --- | --- | --- |
| 安装 unit | `sudo cp deploy/systemd/memory-mcp.service /etc/systemd/system/`<br>`sudo systemctl daemon-reload` | `systemctl cat memory-mcp.service` |
| 启动并启用 | `sudo systemctl enable --now memory-mcp.service` | `sudo systemctl status memory-mcp.service` |
| 健康检查 | `curl --fail http://127.0.0.1:8765/health` | `storage: postgresql`，`maintenance.state: ok` |
| 查看日志 | `sudo journalctl -u memory-mcp.service -f` | 无 ERROR |

- `maintenance.state` 应从 `starting` 进入 `ok`；`degraded` 表示维护循环失败，但数据库健康时 HTTP 仍为 200。
- `MEMORY_MCP_MAINTENANCE_INTERVAL_SECONDS=0` 时状态为 `disabled`。

## 8. 直接访问与公网入口

| 接入方式 | 地址 | 要求 |
| --- | --- | --- |
| VPC/VPN 直连 | `http://<ECS_PRIVATE_IP>:8765/mcp` | `MEMORY_MCP_HOST=0.0.0.0`，安全组限制来源 |
| 公网 ALB/CLB | `https://memory.example.com/mcp` | 443 + 有效证书；后端 `http://<ECS_PRIVATE_IP>:8765`；转发 GET/POST/`Authorization`；关闭代理缓冲；ECS 安全组只允许 LB 安全组访问 8765 |

## 9. Agent 接入

| 步骤 | 命令 | 验证 |
| --- | --- | --- |
| 构建 Agent wheel | `uv build --package memory-mcp-agent --wheel` | 生成 `memory_mcp_agent-<version>-py3-none-any.whl` |
| 安装 Agent | `uv tool install /path/to/memory_mcp_agent-0.2.0-py3-none-any.whl` | `command -v memory-mcp-hook` |
| 配置 Agent env | 编辑 Agent Host 上的 env 文件 | 见下方示例 |

```dotenv
MEMORY_MCP_URL=https://memory.example.com/mcp
MEMORY_MCP_TOKEN=REPLACE_WITH_THIS_AGENT_TOKEN_AT_LEAST_32_CHARACTERS
```

- Agent 包只要求 Python 3.11+，不安装 Server、数据库 driver、LangChain、模型 Provider、ASGI Server 或 migration 命令。
- 不要把整个 Server 仓库部署到 Agent Host。
- `MEMORY_MCP_TOKEN` 必须匹配 Server Principal 映射中的一枚 key。
- 同一用户跨 Agent 配置不同 Token，但映射到相同 tenant/subject/owner；不同用户不得共享 owner。

不同 MCP Host 的概念配置：

```json
{
  "mcpServers": {
    "agent-memory": {
      "type": "streamableHttp",
      "url": "http://10.0.1.10:8765/mcp",
      "headers": {"Authorization": "Bearer REPLACE_WITH_RANDOM_TOKEN"}
    }
  }
}
```

确定性调用流程由 Agent Hook 保证：

```text
BeforeRun → recall_memory → 注入 rendered_context
AfterRun  → capture_completed_turn（仅成功完成的轮次）
```

- 投研 Profile 启用 `relation_policies` 时，Server 在同一 Capture 事务完成候选准入、关系抽取和保存。Agent 不主动调用 `link_memories`。
- `memory-mcp-hook` 内置 Codex/Claude Code 字段兼容。单进程 Agent Framework 直接使用 `MemoryHookBridge`/`HookedAgentRunner`。详见 [Agent 主动记忆接入](agents.md)。

## 10. 发布与回滚

| 步骤 | 命令 | 验证 |
| --- | --- | --- |
| 1 上传代码 | 同步代码到 `/opt/memory-mcp` | `git rev-parse HEAD` |
| 2 安装依赖 | `uv sync --frozen --no-dev --package memory-mcp` | `.venv/bin/memory-mcp --help` |
| 3 迁移 | `sudo systemctl start memory-mcp-migrate.service` | `memory-mcp-db health` |
| 4 重启服务 | `sudo systemctl restart memory-mcp.service` | `systemctl status memory-mcp.service` |
| 5 健康检查 | `curl --fail http://127.0.0.1:8765/health` | HTTP 200 |
| 6 工具发现 | 从 Agent 网络用 MCP Client 执行 `tools/list` | 返回工具列表 |
| 7 功能验证 | 执行跨 Agent 捕获、召回和跨用户负向测试 | 符合预期 |

- migration 只有一个文件 `0001_memory_schema.sql`。开发改 schema 直接修改该文件并用 `memory-mcp-db migrate --rebuild` 重建；生产不用 `--rebuild`。
- 回滚：恢复上一个代码版本并重新同步依赖。Agent Client 独立按 wheel 版本升级或回滚。
- 已成功提交的 migration 不做破坏性降级；migration 失败时新应用版本不得启动。

## 11. 故障排查

| 现象 | 处理 |
| --- | --- |
| model name/key 缺失 | 补齐 `MEMORY_MCP_MODEL_*`；生产不降级为替身 |
| `invalid_candidate_output` | 检查模型 schema、原文 Evidence 和 Profile 类型 |
| `reprocess_required` | command Hook 保留 payload，后续 Stop 有界重试；手工 Client 复用相同 event |
| `not_authorized` / `forbidden` | 检查 Token 映射与 scope |
| 同 owner 召回为 0 | 省略 subject，检查保存状态、Profile 和 query |
| run key conflict | 不同 payload 须使用新的顶层 turn ID |
| AfterRun 较慢 | 当前有界 receipt + 本地 outbox；只有跨主机削峰/持续后台投递才引入 queue |
| 日志出现正文 | 关闭 `MEMORY_MCP_LOG_CONTENT` 并清理内容日志 |
| `maintenance.state: degraded` | 检查 PostgreSQL 连接和维护周期；数据库健康时 HTTP 仍 200 |

日志字段与脱敏见[日志规范](logging.md)。

## 12. 当前边界

- Bearer Token 映射是静态认证适配器，不是 OAuth/OIDC。
- Recall 使用 `pg_trgm` 词法 + 可选 pgvector 向量 + 近期三路候选；向量路未配置
  `MEMORY_MCP_EMBEDDING_API_KEY` 时降级为词法+近期两路。候选硬上限约束应用层载入，
  不能用无限调大配置替代容量评测。
- 周期维护与团队提取在 Server lifespan 内运行，不增加额外 systemd unit、队列或 Agent 配置。
- PostgreSQL 是唯一运行时存储，不提供 SQLite 降级路径。
- 部署到新 RDS 实例时仍需先在隔离测试库执行验收套件。
