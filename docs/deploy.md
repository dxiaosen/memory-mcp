# Memory MCP 部署

本文描述本项目的 P0 部署形态：Memory MCP 直接运行在 Linux ECS 上。同一
VPC/VPN 内的 Agent 直接访问 ECS 私网服务地址；公网 Agent 通过可选的阿里云
ALB/CLB HTTPS 入口访问。服务通过 VPC 私网连接托管 PostgreSQL，不安装
Docker 或 Nginx。

仓库的 `deploy/` 目录只存放运维制品，目前是两个 systemd unit：一个长期运行
Server，一个一次性执行 migration。它不是应用代码包、运行数据目录或容器目录。

## 1. 目标拓扑

```text
VPC/VPN 内 Agent ── HTTP + Authorization ───────┐
                                                 │
公网 Agent ── HTTPS ── 可选 ALB/CLB ── 私网 HTTP ┤
                                                 ▼
                              Memory MCP <ECS_PRIVATE_IP>:8765
                                      systemd 管理
                                                 │
                                          VPC 私网连接
                                                 ▼
                                          RDS PostgreSQL
```

私网直连和 ALB/CLB 后端都使用同一个 MCP 服务地址，不增加应用层转发组件。
安全组只允许可信 Agent 私网网段和可选负载均衡器访问 `8765`。

## 2. 前置条件

- ECS 与 PostgreSQL 位于同地域、同 VPC，或已经建立可控的私网路由；
- PostgreSQL 创建独立数据库和最小权限应用账号；
- 公网接入时，ALB/CLB 已配置域名和有效 TLS 证书；
- Linux 已安装 `uv`；
- 项目文件部署到 `/opt/memory-mcp`；
- 服务器时间同步正常。

本期不使用 PostgreSQL 向量扩展，不要求额外数据库插件。

## 3. 网络与安全组

ECS 入方向建议：

| 端口 | 来源 | 用途 |
| --- | --- | --- |
| `22` | 管理员固定 IP | 运维 SSH |
| `8765` | 可信 VPC/VPN Agent 网段；可选 ALB/CLB 安全组 | MCP 服务直连 |

如果启用 ALB/CLB，公网 `443` 配置在负载均衡器安全组，不配置在 ECS 安全组。
不要把 ECS `8765` 开放给 `0.0.0.0/0`。

PostgreSQL 只允许 ECS 安全组或 ECS 私网地址访问数据库端口。不要为调试临时开放
全网数据库访问。

## 4. 安装应用

创建独立系统用户和目录：

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin memory-mcp
sudo mkdir -p /opt/memory-mcp /etc/memory-mcp /var/log/memory-mcp
sudo chown -R memory-mcp:memory-mcp /opt/memory-mcp /var/log/memory-mcp
sudo chown root:memory-mcp /etc/memory-mcp
sudo chmod 750 /etc/memory-mcp
```

将代码同步到 `/opt/memory-mcp` 后安装锁定依赖：

```bash
cd /opt/memory-mcp
sudo -u memory-mcp uv sync --frozen --no-dev --package memory-mcp
```

项目使用 Python 3.14。目标机器不必预先通过系统包安装同版本 Python，`uv` 可以
按项目声明准备隔离环境。必须指定 `--package memory-mcp`；workspace 默认可能
包含其他 member，Server 生产环境不应安装 Agent 发行包。

## 5. 运行配置

把 `server/.env.example` 复制为 `/etc/memory-mcp/memory-mcp.env`，只保留服务器实际需要
的值。示例：

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

MEMORY_MCP_AUTH_ISSUER_URL=https://memory.example.com/auth
MEMORY_MCP_RESOURCE_SERVER_URL=https://memory.example.com/mcp
MEMORY_MCP_AUTH_TOKENS='{"REPLACE_WITH_RANDOM_TOKEN_AT_LEAST_32_CHARACTERS":{"tenant_id":"tenant-001","subject_id":"subject-001","scopes":["memory:read","memory:write","memory:review"]}}'

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

数据库密码中的 `@`、`:`、`/`、`#` 等字符必须做 URL 编码。不要把数据库 URL、
Bearer Token 或模型 API Key 写进 Git、systemd unit 或命令行参数。
每枚静态 Token 必须至少 32 字符，且不同 Agent 使用不同随机值。
部署环境应保持 `LOG_CONTENT=false`；只在受控手工联调窗口临时开启，并在结束后
关闭和清理内容日志。

设置权限：

```bash
sudo chown root:memory-mcp /etc/memory-mcp/memory-mcp.env
sudo chmod 640 /etc/memory-mcp/memory-mcp.env
```

## 6. 数据库迁移

迁移是独立发布步骤，应用默认不会在启动时自动修改 schema：

```bash
sudo cp deploy/systemd/memory-mcp-migrate.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start memory-mcp-migrate.service
sudo systemctl status memory-mcp-migrate.service
```

迁移记录 checksum；已经执行过的 migration 文件如果被修改，后续迁移会拒绝
继续执行。数据库迁移成功后可单独验证：

```bash
sudo -u memory-mcp /bin/bash -c \
  'set -a; source /etc/memory-mcp/memory-mcp.env; set +a; exec /opt/memory-mcp/.venv/bin/memory-mcp-db health'
```

健康命令会验证连接、必需表、migration 版本和 checksum。重复执行 migration
unit 应报告 schema 已是最新状态。

## 7. 启动 systemd 服务

```bash
sudo cp deploy/systemd/memory-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now memory-mcp.service
sudo systemctl status memory-mcp.service
curl --fail http://127.0.0.1:8765/health
```

查看日志：

```bash
sudo journalctl -u memory-mcp.service -f
```

健康响应中的 `storage` 应为 `postgresql`。健康接口只返回运行元数据，不返回
数据库地址、Token 或记忆正文。

## 8. 直接访问服务

可信 VPC/VPN 内的 Agent 直接配置：

```text
http://<ECS_PRIVATE_IP>:8765/mcp
```

这就是 MCP 服务本身的地址，不需要 Nginx、网关或额外应用进程。远程访问要求
`MEMORY_MCP_HOST=0.0.0.0`，安全组必须限制来源。

如果 Agent 位于公网，则使用可选 ALB/CLB：

- 公网监听器使用 `443` 和有效证书；
- 后端指向 `http://<ECS_PRIVATE_IP>:8765`；
- ECS 安全组只允许负载均衡器安全组访问 `8765`；
- 转发 GET、POST 和 `Authorization` 请求头；
- 关闭会破坏 Streamable HTTP 的代理缓冲。

不得在公网使用带 Bearer Token 的明文 HTTP。

## 9. Agent 直接接入

Server 的 `/etc/memory-mcp/memory-mcp.env` 不包含 Agent Hook 配置。每个 Agent
Host 在自己的机器或编排单元中单独注入：

```dotenv
MEMORY_MCP_URL=https://memory.example.com/mcp
MEMORY_MCP_TOKEN=REPLACE_WITH_THIS_AGENT_TOKEN_AT_LEAST_32_CHARACTERS
```

这个文件属于 Agent 部署，不应复制到 Memory MCP Server，也不应包含其他 Agent
的 Token。`MEMORY_MCP_TOKEN` 必须匹配 Server Principal 映射中的一枚 key。
`profile_id`、owner、client/Agent ID、超时、预算和重试使用代码默认值，不要求普通
Agent 用户配置。

主动 Hook 还要求 Agent Host 安装独立轻量发行物。推荐在发布机先构建：

```bash
uv build --package memory-mcp-agent --wheel
```

把生成的 `memory_mcp_agent-<version>-py3-none-any.whl` 作为版本化制品分发到
Agent Host，再安装：

```bash
uv tool install /path/to/memory_mcp_agent-0.1.0-py3-none-any.whl
command -v memory-mcp-hook
```

若使用组织 Python registry，则安装固定版本
`uv tool install memory-mcp-agent==0.1.0`。Agent 包只要求 Python 3.11+，不会
安装 `memory-mcp`、数据库 driver、LangChain、模型 Provider、ASGI Server 或
migration 命令。不要为了得到 Hook 命令把整个 Server 仓库部署到 Agent Host。

不同 MCP Host 的配置字段可能不同，概念配置如下：

```json
{
  "mcpServers": {
    "agent-memory": {
      "type": "streamableHttp",
      "url": "http://10.0.1.10:8765/mcp",
      "headers": {
        "Authorization": "Bearer REPLACE_WITH_RANDOM_TOKEN"
      }
    }
  }
}
```

公网接入时只需把 URL 换成 ALB/CLB 提供的
`https://memory.example.com/mcp`，MCP 工具契约不变。

连接 MCP 只表示 Host 可以发现工具。主动记忆的确定性调用流程仍应由 Agent Hook
保证：

```text
BeforeRun  → recall_memory → 注入 rendered_context
AfterRun   → capture_completed_turn（仅成功完成的轮次）
```

`memory-mcp-hook` 接受通用 BeforeRun/AfterRun 合同，并内置 Codex/Claude Code
字段兼容，不需要复制客户端实现。Codex 当前没有原生 HTTP Hook，因此跨机时也由
这个本地轻量命令请求远端 Server；它不是本地 Server。Host 安装、标准合同、首批
配置模板、信任步骤和手工闭环见[Agent 主动记忆接入](agents.md)。单进程 Agent
Framework 可以直接使用 `MemoryHookBridge`/`HookedAgentRunner`。

同一用户通过不同 Agent 接入时，可以配置不同 Token，但这些 Token 必须映射到同一
tenant/subject/owner；不同用户不得共享 owner。`owner_id` 永远不作为工具参数。

## 10. 发布与回滚

推荐发布顺序：

1. 上传新代码；
2. 执行 `uv sync --frozen --no-dev --package memory-mcp`；
3. 运行 migration oneshot unit；
4. 重启 MCP 服务；
5. 检查本机健康接口；
6. 从目标 Agent 网络使用真实 MCP Client 执行 `tools/list`；
7. 执行一次跨 Agent 捕获、召回和跨用户负向测试。

`0003_profile_naming.sql` 是保留数据的命名迁移：把旧 profile 相关表和
`scenario/policy_version` 列原地重命名为 `profile_id/profile_version`。
`0004_memory_metadata.sql` 在保留旧 revision/Evidence 的基础上增加 confidence、
verification、sensitivity、validity 和 citation 字段；
`0005_metadata_rollback_compat.sql` 保证旧版 Server 短期回滚仍可写入。不要修改任何已执行 migration，
否则已部署数据库会因 checksum 不一致拒绝启动。由于 MCP
DTO 同步改为 `profile_id`，Server 与主动记忆 Agent Client 应在同一发布窗口升级。

Server 应用回滚时恢复上一个代码版本并重新同步锁定依赖。Agent Client 独立按
wheel 版本升级或回滚，不要求与 Server 同机操作；发布前必须通过兼容性测试。
已经成功提交的向前兼容数据库 migration 不做破坏性降级；如果 migration 失败，
新应用版本不得启动。

## 11. 当前边界

- 当前 Bearer Token 映射是静态认证适配器，不是生产 OAuth/OIDC；
- 当前默认单实例，不声称完成多 worker 或自动伸缩；
- PostgreSQL 是唯一运行时存储，不提供 SQLite 降级路径；
- PostgreSQL migration、Repository 和真实 RDS 集成验收已经完成；部署到新的
  RDS 实例时仍需先在隔离测试库执行同一验收套件。
