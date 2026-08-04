# Memory MCP 端到端使用（开发环境）

面向开发者和第一次使用者，从空环境跑通 Server、数据库、真实模型和一个 Agent Host。**生产部署见 [部署指南](deploy.md)。** 全部配置项见[配置参考](config.md)，宿主 Hook 配置见[Agent 主动记忆](agents.md)。

## 1. 拓扑

```text
Agent Host ── BeforeRun/AfterRun ──▶ Memory MCP Server
  (URL + Token)                      PostgreSQL + 真实模型 + 认证
```

- Agent 与 Server 可不同机器。
- Agent Host 只安装轻量 Client；模型提取、身份派生和持久化都在 Server。

## 2. 安装与配置

```bash
uv sync --all-packages --frozen
cp server/.env.example .env && chmod 600 .env
cp agent/.env.example examples/agent.env && chmod 600 examples/agent.env
```

- `.env`：PostgreSQL DSN、`MEMORY_MCP_AUTH_TOKENS`、`MEMORY_MCP_MODEL_*`。
- `examples/agent.env`：只填 URL 和 Token。

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
.venv/bin/python examples/client.py --env-file examples/agent.env tools
```

- `PostgreSQL schema is up to date` 表示 migration 与 checksum 已同步。
- 源码移动后重新运行 `uv sync --all-packages --frozen`。

## 4. 真实模型闭环

```text
写入记忆 ──hook_runner.py──▶ capture_status 已完成, created_memory_ids 非空
召回记忆 ──hook_runner.py──▶ 返回匹配记忆
```

```bash
# 写入
.venv/bin/python examples/hook_runner.py \
  --env-file examples/agent.env \
  --conversation-id atlas-write --turn-id atlas-write-1 \
  --input '在 Atlas 项目中，架构决策记录默认使用中文，并长期保持。'

# 召回
.venv/bin/python examples/hook_runner.py \
  --env-file examples/agent.env \
  --conversation-id atlas-read --turn-id atlas-read-1 \
  --task-intent '查询项目文档约定' \
  --input 'Atlas 架构决策记录使用什么语言？'
```

- 验证先省略 `--subject`（精确预过滤器，仅宿主和抽取器共享规范枚举时传入）。
- 召回为 0 时检查 capture 结果、pending 状态、Profile、Token 映射和查询文本。

## 5. 投研 Profile 与关系

Server 同时注册 `general-work` 和 `investment-research`，但不会根据正文猜测场景。投研产品应在 `MEMORY_MCP_AUTH_TOKENS` 中把 `default_profile_id` 固定为 `investment-research`。

```bash
MEMORY_HOOK_PROFILE_ID=investment-research \
  .venv/bin/python examples/hook_runner.py \
  --env-file examples/agent.env \
  --conversation-id research-write --turn-id research-write-1 \
  --input '我长期要求投研结论同时列出支持证据和反方风险。'
```

- 投研 AfterRun 自动识别明确、高置信且方向合法的关系，与本轮记忆在同一事务保存。
- `link_memories` 与 `revoke_memory_relation` 保留为历史补链和人工治理工具，普通 Agent 不必主动调用。详见[详细总设计](design.md)。

## 6. 多 Agent 共享与用户隔离

```text
Agent A / Agent B  → tenant-001 / subject-001  ── 共享 owner
User B Agent B     → tenant-001 / subject-002    独立 owner
```

- 同一用户不同 Agent 发放不同 Token，映射到相同 `tenant_id/subject_id`，共享 owner。
- 不同用户用不同 subject，自然隔离。

## 7. 只读检查与治理

```bash
.venv/bin/python examples/client.py --env-file examples/agent.env tools
.venv/bin/python examples/client.py --env-file examples/agent.env memories
.venv/bin/python examples/client.py --env-file examples/agent.env pending
.venv/bin/python examples/client.py --env-file examples/agent.env recall \
  --profile-id general-work --query '项目文档偏好'
```

- Client 还可调用 confirm/reject、`revoke_memory`、`link_memories` 和 `revoke_memory_relation`，全部 owner-scoped。
- 撤销保留 revision、Evidence 和关系历史，不物理删除。
- 到期记忆在读取时先过滤；Server runner 随后物化为 `expired`，终止超 30 天的 pending review，标记相关关系为 `stale/endpoint_expired`。
- 无公共工具，不要求主动触发。

## 8. 自动化与评测

```bash
.venv/bin/python -m evals.runner              # 确定性链路，不读 DB 不调模型
.venv/bin/python -m evals.runner --live-model # 真实模型评测
```

详见[测试与验收](testing.md)和[投研记忆评测](evaluation.md)。故障排查见[部署指南](deploy.md#11-故障排查)。
