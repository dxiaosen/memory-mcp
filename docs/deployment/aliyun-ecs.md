# 阿里云 ECS 远程 MCP 部署

本文描述本项目的 P0 部署形态：Memory MCP 直接运行在 Linux ECS 上，通过公网
HTTPS 向不同 Agent Host 提供 Streamable HTTP MCP，并通过 VPC 私网连接托管
PostgreSQL。阿里云百炼不是必需组件，Docker 和 Nginx 也不是 MCP 协议依赖。

## 1. 目标拓扑

```text
Codex / LangChain / 自研 Agent / MCP Client
                    │
          HTTPS + Authorization
                    ▼
       TLS 终止层（以下任选一种）
       - ECS 上的 Nginx
       - 阿里云 ALB/CLB
       - 其他可信反向代理
                    │
                    ▼
       Memory MCP 127.0.0.1:8765
              systemd 管理
                    │
             VPC 私网连接
                    ▼
             RDS PostgreSQL
```

如果 TLS 由 ALB/CLB 终止，ECS 不需要安装 Nginx，但安全组只能允许负载均衡器
访问 MCP 的私网监听端口。如果 TLS 在同机终止，MCP 应保持监听
`127.0.0.1:8765`。

## 2. 前置条件

- ECS 与 PostgreSQL 位于同地域、同 VPC，或已经建立可控的私网路由；
- PostgreSQL 创建独立数据库和最小权限应用账号；
- ECS 具有域名与有效 TLS 证书，或前方已有提供 HTTPS 的负载均衡器；
- Linux 已安装 `uv`；
- 项目文件部署到 `/opt/agent-lab`；
- 服务器时间同步正常。

本期不使用 PostgreSQL 向量扩展，不要求额外数据库插件。

## 3. 网络与安全组

ECS 入方向建议：

| 端口 | 来源 | 用途 |
| --- | --- | --- |
| `443` | 需要接入的公网客户端 | HTTPS MCP |
| `22` | 管理员固定 IP | 运维 SSH |
| `8765` | 不开放公网 | MCP 应用内部端口 |

PostgreSQL 只允许 ECS 安全组或 ECS 私网地址访问数据库端口。不要为调试临时开放
全网数据库访问。

## 4. 安装应用

创建独立系统用户和目录：

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin agent-lab
sudo mkdir -p /opt/agent-lab /etc/agent-lab /var/log/agent-lab
sudo chown -R agent-lab:agent-lab /opt/agent-lab /var/log/agent-lab
sudo chmod 750 /etc/agent-lab
```

将代码同步到 `/opt/agent-lab` 后安装锁定依赖：

```bash
cd /opt/agent-lab
sudo -u agent-lab uv sync --frozen --no-dev
```

项目使用 Python 3.14。目标机器不必预先通过系统包安装同版本 Python，`uv` 可以
按项目声明准备隔离环境。

## 5. 运行配置

把 `.env.example` 复制为 `/etc/agent-lab/memory-mcp.env`，只保留服务器实际需要
的值。示例：

```dotenv
MEMORY_MCP_STORAGE_BACKEND=postgresql
MEMORY_MCP_DATABASE_URL='postgresql://memory_app:URL_ENCODED_PASSWORD@RDS_PRIVATE_HOST:5432/agent_lab?sslmode=require'
MEMORY_MCP_DATABASE_POOL_MIN_SIZE=1
MEMORY_MCP_DATABASE_POOL_MAX_SIZE=5
MEMORY_MCP_DATABASE_CONNECT_TIMEOUT_SECONDS=10
MEMORY_MCP_DATABASE_MIGRATE_ON_STARTUP=false

MEMORY_MCP_HOST=127.0.0.1
MEMORY_MCP_PORT=8765
MEMORY_MCP_MCP_PATH=/mcp
MEMORY_MCP_HEALTH_PATH=/health

MEMORY_MCP_DEMO_TOKENS_JSON='{"REPLACE_WITH_RANDOM_TOKEN":{"owner_key":"demo-user-a","tenant_id":"demo","subject_id":"demo-user-a","client_id":"agent-a","agent_id":"agent-a","scopes":["memory:read","memory:write","memory:review"]}}'

MEMORY_MCP_LOG_LEVEL=INFO
MEMORY_MCP_LOG_FILE=/var/log/agent-lab/memory-mcp.log
```

数据库密码中的 `@`、`:`、`/`、`#` 等字符必须做 URL 编码。不要把数据库 URL、
Bearer Token 或模型 API Key 写进 Git、systemd unit、命令行参数或 Nginx 配置。

设置权限：

```bash
sudo chown root:agent-lab /etc/agent-lab/memory-mcp.env
sudo chmod 640 /etc/agent-lab/memory-mcp.env
```

## 6. 数据库迁移

迁移是独立发布步骤，应用默认不会在启动时自动修改 schema：

```bash
sudo cp deploy/systemd/agent-lab-memory-migrate.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start agent-lab-memory-migrate.service
sudo systemctl status agent-lab-memory-migrate.service
```

迁移记录 checksum；已经执行过的 migration 文件如果被修改，后续迁移会拒绝
继续执行。数据库迁移成功后可单独验证：

```bash
sudo systemctl start agent-lab-memory-migrate.service
```

重复执行应报告 schema 已是最新状态。

## 7. 启动 systemd 服务

```bash
sudo cp deploy/systemd/agent-lab-memory.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agent-lab-memory.service
sudo systemctl status agent-lab-memory.service
curl --fail http://127.0.0.1:8765/health
```

查看日志：

```bash
sudo journalctl -u agent-lab-memory.service -f
```

健康响应中的 `storage` 应为 `postgresql`。健康接口只返回运行元数据，不返回
数据库地址、Token 或记忆正文。

## 8. HTTPS 终止

### 8.1 使用同机 Nginx

仓库提供 `deploy/nginx/agent-lab-memory.conf.example`。替换域名和证书路径后：

```bash
sudo cp deploy/nginx/agent-lab-memory.conf.example \
  /etc/nginx/conf.d/agent-lab-memory.conf
sudo nginx -t
sudo systemctl reload nginx
```

示例关闭了响应和请求缓冲，并提高了流式读取超时。Nginx 默认会转发
`Authorization` 请求头，不要把该请求头加入 access log。

### 8.2 不使用 Nginx

由 ALB/CLB 或其他可信代理提供 HTTPS：

- 公网监听器使用 `443` 和有效证书；
- 后端只指向 ECS 私网地址和受限 MCP 端口；
- 安全组只允许负载均衡器访问该端口；
- 转发 GET、POST 和 MCP 所需请求头；
- 关闭会破坏流式响应的代理缓冲。

不得为了省略 TLS 终止层而在公网使用带 Bearer Token 的明文 HTTP。

## 9. Agent 直接接入

不同 MCP Host 的配置字段可能不同，概念配置如下：

```json
{
  "mcpServers": {
    "agent-memory": {
      "type": "streamableHttp",
      "url": "https://memory.example.com/mcp",
      "headers": {
        "Authorization": "Bearer REPLACE_WITH_RANDOM_TOKEN"
      }
    }
  }
}
```

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
6. 从公网使用真实 MCP Client 执行 `tools/list`；
7. 执行一次跨 Agent 捕获、召回和跨用户负向测试。

应用回滚时恢复上一个代码版本并重新同步锁定依赖。已经成功提交的向前兼容
数据库 migration 不做破坏性降级；如果 migration 失败，新应用版本不得启动。

## 11. 当前边界

- 当前 Bearer Token 映射用于原型身份验证，不是生产 OAuth；
- 当前默认单实例，不声称完成多 worker 或自动伸缩；
- SQLite 仅保留到 PostgreSQL 真实契约测试完成，不进入 ECS 正式部署；
- PostgreSQL migration 和 Repository 已实现，但首次接入实际 RDS 后仍必须运行
  集成验收，未运行前不能宣称云数据库迁移已经验收完成。
