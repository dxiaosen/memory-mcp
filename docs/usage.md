# Memory MCP 端到端使用（开发环境）

面向开发者和第一次使用者，从空环境跑通 Server、数据库和真实模型。**生产部署见 [部署指南](deploy.md)。** 全部配置项见[配置参考](config.md)，宿主 Hook 配置见[Agent 主动记忆](agents.md)。

## 1. 拓扑

Agent Host 通过 BeforeRun/AfterRun Hook 与 Memory MCP Server 交互；Agent 与 Server 可不同
机器。Agent Host 只安装轻量 Client；模型提取、身份派生和持久化都在 Server。架构图见
[docs/README.md](README.md)。

## 2. 安装与配置

```bash
uv sync --all-packages --frozen
cp server/.env.example .env && chmod 600 .env
```

- `.env`：PostgreSQL DSN、`MEMORY_MCP_AUTH_TOKENS`、`MEMORY_MCP_MODEL_*`。
- Agent Host 单独配置 `MEMORY_MCP_URL` 与 `MEMORY_MCP_TOKEN`（必须是服务端 Token 映射中的一枚 key）。

```dotenv
MEMORY_MCP_URL=http://127.0.0.1:8765/mcp
MEMORY_MCP_TOKEN=<服务端已映射的高熵 Token>
```

## 3. 启动 Server

```bash
.venv/bin/memory-mcp-db migrate
.venv/bin/memory-mcp-db health
.venv/bin/memory-mcp
```

另一个终端验证：

```bash
curl --fail http://127.0.0.1:8765/health
```

- `Memory PostgreSQL is healthy` 表示 migration checksum 一致、扩展可用且必需索引存在。
- 源码移动后重新运行 `uv sync --all-packages --frozen`。

## 4. 真实模型闭环

```text
召回记忆 ──BeforeRun Hook 调 recall_memory──▶ 返回匹配记忆，注入 Agent 上下文
写入记忆 ──Stop hook 强制入队 capture_completed_turn──▶ 异步抽取落库
```

召回由 Agent Host 的 BeforeRun Hook 自动触发，向 Server 调 `recall_memory`，返回的 `rendered_context` 注入下一轮 prompt；捕获由 Stop hook 把整轮对话透传给 Server `capture_completed_turn`，服务端队列异步抽取。

- 召回为 0 时检查 capture 结果、pending 状态、Profile、Token 映射和查询文本。
- Hook 具体接入方式见[Agent 主动记忆](agents.md)。

## 5. 投研 Profile 与关系

Server 同时注册 `general-work` 和 `investment-research`，但不会根据正文猜测场景。投研产品应在 `MEMORY_MCP_AUTH_TOKENS` 中把 `default_profile_id` 固定为 `investment-research`。

- 投研 AfterRun 自动识别明确、高置信且方向合法的关系，与本轮记忆在同一事务保存。
- `link_memories` 与 `revoke_memory_relation` 保留为历史补链和人工治理工具，普通 Agent 不必主动调用。详见[详细总设计](design.md)。

## 6. 多 Agent 共享与用户隔离

```text
Agent A / Agent B  → tenant-001 / subject-001  ── 共享 owner
User B Agent B     → tenant-001 / subject-002    独立 owner
```

- 同一用户不同 Agent 发放不同 Token，映射到相同 `tenant_id/subject_id`，共享 owner。
- 不同用户用不同 subject，自然隔离。

## 7. 治理

- 撤销保留 revision、Evidence 和关系历史，不物理删除。
- 到期记忆在读取时先过滤；Server runner 随后物化为 `expired`，终止超 30 天的 pending review，标记相关关系为 `stale/endpoint_expired`。
- 无公共工具，不要求主动触发。

故障排查见[部署指南](deploy.md#11-故障排查)。
