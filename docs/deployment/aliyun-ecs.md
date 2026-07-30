# 阿里云 ECS 远程 MCP 部署

本文描述本项目的 P0 部署形态：Memory MCP 直接运行在 Linux ECS 上。同一
VPC/VPN 内的 Agent 直接访问 ECS 私网服务地址；公网 Agent 通过可选的阿里云
ALB/CLB HTTPS 入口访问。服务通过 VPC 私网连接托管 PostgreSQL，不安装
Docker 或 Nginx。

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
sudo -u memory-mcp uv sync --frozen --no-dev
```

项目使用 Python 3.14。目标机器不必预先通过系统包安装同版本 Python，`uv` 可以
按项目声明准备隔离环境。

## 5. 运行配置

把 `.env.example` 复制为 `/etc/memory-mcp/memory-mcp.env`，只保留服务器实际需要
的值。示例：

```dotenv
MEMORY_MCP_STORAGE_BACKEND=postgresql
MEMORY_MCP_DATABASE_URL='postgresql://memory_app:URL_ENCODED_PASSWORD@RDS_PRIVATE_HOST:5432/memory_mcp?sslmode=require'
MEMORY_MCP_DATABASE_POOL_MIN_SIZE=1
MEMORY_MCP_DATABASE_POOL_MAX_SIZE=5
MEMORY_MCP_DATABASE_CONNECT_TIMEOUT_SECONDS=10
MEMORY_MCP_DATABASE_MIGRATE_ON_STARTUP=false

MEMORY_MCP_HOST=0.0.0.0
MEMORY_MCP_PORT=8765
MEMORY_MCP_MCP_PATH=/mcp
MEMORY_MCP_HEALTH_PATH=/health

MEMORY_MCP_DEMO_TOKENS_JSON='{"REPLACE_WITH_RANDOM_TOKEN":{"owner_key":"demo-user-a","tenant_id":"demo","subject_id":"demo-user-a","client_id":"agent-a","agent_id":"agent-a","scopes":["memory:read","memory:write","memory:review"]}}'

MEMORY_MCP_LOG_LEVEL=INFO
MEMORY_MCP_LOG_FILE=/var/log/memory-mcp/memory-mcp.log
```

数据库密码中的 `@`、`:`、`/`、`#` 等字符必须做 URL 编码。不要把数据库 URL、
Bearer Token 或模型 API Key 写进 Git、systemd unit 或命令行参数。

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
或外层 Runner 保证：

```text
BeforeRun  → recall_memory → 注入 rendered_context
AfterRun   → capture_completed_turn（仅成功完成的轮次）
```

同一用户通过不同 Agent 接入时，可以配置不同 Token，但这些 Token 必须映射到同一
tenant/subject/owner；不同用户不得共享 owner。`owner_id` 永远不作为工具参数。

## 10. 发布与回滚

推荐发布顺序：

1. 上传新代码；
2. 执行 `uv sync --frozen --no-dev`；
3. 运行 migration oneshot unit；
4. 重启 MCP 服务；
5. 检查本机健康接口；
6. 从目标 Agent 网络使用真实 MCP Client 执行 `tools/list`；
7. 执行一次跨 Agent 捕获、召回和跨用户负向测试。

应用回滚时恢复上一个代码版本并重新同步锁定依赖。已经成功提交的向前兼容
数据库 migration 不做破坏性降级；如果 migration 失败，新应用版本不得启动。

## 11. 当前边界

- 当前 Bearer Token 映射用于原型身份验证，不是生产 OAuth；
- 当前默认单实例，不声称完成多 worker 或自动伸缩；
- SQLite 仅保留到 PostgreSQL 真实契约测试完成，不进入 ECS 正式部署；
- PostgreSQL migration 和 Repository 已实现，但首次接入实际 RDS 后仍必须运行
  集成验收，未运行前不能宣称云数据库迁移已经验收完成。
