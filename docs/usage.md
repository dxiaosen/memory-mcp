# Memory MCP 端到端使用

本文面向**开发者和第一次使用者**，从空环境跑通 Server、数据库、真实模型和一个
Agent Host。生产部署的完整步骤（systemd、安全组、ALB/CLB）见
[部署指南](deploy.md)。全部配置项见[配置参考](config.md)，宿主 Hook 配置见
[Agent 主动记忆](agents.md)。

## 1. 部署拓扑

```text
Agent Host (memory-mcp-agent)
  MEMORY_MCP_URL + MEMORY_MCP_TOKEN
          │ BeforeRun / AfterRun
          ▼
Memory MCP Server (memory-mcp)
  PostgreSQL + 真实模型 + 认证
```

Agent 与 Server 可以位于不同机器。Agent Host 只安装轻量 Client；模型提取、身份
派生和持久化都在 Server。两个进程是否同机不影响协议。

## 2. 安装与配置

仓库开发环境：

```bash
uv sync --all-packages --frozen
cp server/.env.example .env
chmod 600 .env
cp agent/.env.example examples/agent.env
chmod 600 examples/agent.env
```

在 `.env` 中填写 PostgreSQL DSN、`MEMORY_MCP_AUTH_TOKENS` 和
`MEMORY_MCP_MODEL_*`；在 `examples/agent.env` 中只填写：

```dotenv
MEMORY_MCP_URL=http://127.0.0.1:8765/mcp
MEMORY_MCP_TOKEN=<服务端已映射的高熵 Token>
```

生产 Server 与远端 Agent 分别安装自己的发行物。Agent wheel 的构建和安装命令见
[Agent 主动记忆](agents.md#3-安装-hook-命令)。Secret 应由部署平台注入，不放进
命令行、Git 或截图。

## 3. 启动 Server

```bash
.venv/bin/memory-mcp-db migrate
.venv/bin/memory-mcp-db health
.venv/bin/memory-mcp
```

默认地址：

```text
Health: http://127.0.0.1:8765/health
MCP:    http://127.0.0.1:8765/mcp
```

另一个终端检查：

```bash
curl --fail http://127.0.0.1:8765/health
.venv/bin/python examples/client.py \
  --env-file examples/agent.env \
  tools
```

`PostgreSQL schema is up to date` 表示 migration 与 checksum 已同步。源码移动或
console script 变化后，重新运行 `uv sync --all-packages --frozen` 更新开发安装。

## 4. 真实模型闭环

运行一个包含长期偏好的成功顶层轮次：

```bash
.venv/bin/python examples/hook_runner.py \
  --env-file examples/agent.env \
  --conversation-id atlas-write \
  --turn-id atlas-write-1 \
  --input '在 Atlas 项目中，架构决策记录默认使用中文，并长期保持。'
```

检查 `capture_status` 为已知完成状态；`created_memory_ids` 非空表示已自动保存，
保守候选也可能进入 pending。随后用新轮次召回：

```bash
.venv/bin/python examples/hook_runner.py \
  --env-file examples/agent.env \
  --conversation-id atlas-read \
  --turn-id atlas-read-1 \
  --task-intent '查询项目文档约定' \
  --input 'Atlas 架构决策记录使用什么语言？'
```

真实模型验证先省略 `--subject`。`subject` 是精确预过滤器，只有宿主和抽取器共享
规范枚举时才应传入。召回为 0 时依次检查 capture 结果、pending 状态、Profile、
Token 映射和查询文本。

## 5. 投研 Profile 与关系

Server 同时注册 `general-work` 和 `investment-research`，但不会根据正文猜测场景。
投研产品应在 `MEMORY_MCP_AUTH_TOKENS` 中把当前 Agent Token 的
`default_profile_id` 固定为 `investment-research`；Agent 仍只配置 URL 和 Token。
仓库手工验证或高级集成也可临时覆盖：

```bash
MEMORY_HOOK_PROFILE_ID=investment-research \
  .venv/bin/python examples/hook_runner.py \
  --env-file examples/agent.env \
  --conversation-id research-write \
  --turn-id research-write-1 \
  --input '我长期要求投研结论同时列出支持证据和反方风险。'
```

投研 AfterRun 会在候选准入后自动识别明确、高置信且方向合法的关系，并与本轮记忆
在同一事务保存。Assistant/Tool 自述、pending、blocked、低置信或歧义关系不会
自动建边。`link_memories` 与 `revoke_memory_relation` 保留为历史补链和人工治理
工具，不是普通 Agent 必须主动调用的步骤。

投研类型、关系方向、revision 绑定和 stale 规则见[详细总设计](design.md)，质量测试
见[投研记忆评测](evaluation.md)。

## 6. 多 Agent 共享与用户隔离

为同一用户的不同 Agent 发放不同 Token，并在 Server 端映射到相同
`tenant_id/subject_id`；它们会共享 owner。不同用户使用不同 subject，自然隔离。

手工验收建议准备三个独立 Agent env 文件：

```text
Agent A        -> tenant-001 / subject-001
Agent B        -> tenant-001 / subject-001
User B Agent B -> tenant-001 / subject-002
```

用 Agent A 写入，同 owner Agent B 应能召回；User B 使用相同 query 应返回 0。每个
Agent 进程只读取自己的 URL 和 Token，不能把多枚 Token 合并进同一配置。

## 7. 只读检查与治理

```bash
.venv/bin/python examples/client.py --env-file examples/agent.env tools
.venv/bin/python examples/client.py --env-file examples/agent.env memories
.venv/bin/python examples/client.py --env-file examples/agent.env pending
.venv/bin/python examples/client.py \
  --env-file examples/agent.env \
  recall \
  --profile-id general-work \
  --query '项目文档偏好'
```

完整 MCP Client 还可以调用 confirm/reject、`revoke_memory`、`link_memories` 和
`revoke_memory_relation`。这些工具全部 owner-scoped；撤销保留 revision、Evidence
和关系历史，不执行物理删除。

到期记忆即使维护尚未运行，也会先在读取时被过滤；Server runner 随后把 revision
物化为 `expired`，终止到期或超过 30 天的 pending review，并把相关活动关系标记为
`stale/endpoint_expired`。该流程没有公共工具，不要求 Agent 或用户主动触发。

## 8. 自动化与评测

无需模型网络的确定性链路、专用 PostgreSQL 测试和发布检查统一见
[测试与验收](testing.md)。默认投研评测：

```bash
.venv/bin/python -m evals.runner
```

它不会读取数据库或调用模型。真实模型评测必须显式增加 `--live-model`，结果说明见
[投研记忆评测](evaluation.md)。

## 9. 故障排查

网络拓扑和公网 HTTPS 配置见[部署指南](deploy.md)，日志字段与脱敏见
[日志规范](logging.md)。

| 现象 | 处理 |
| --- | --- |
| 服务提示 model name/key 缺失 | 补齐 `MEMORY_MCP_MODEL_*`；生产不会降级为替身 |
| `invalid_candidate_output` | 检查模型 schema、原文 Evidence 和 Profile 类型 |
| `reprocess_required` | command Hook 会保留固定 payload，并在后续 Stop 有界重试；手工 Client 复用相同 event |
| `not_authorized` / `forbidden` | 检查 Token 映射与 scope |
| 同 owner 召回为 0 | 省略 subject，再检查保存状态、Profile 和 query |
| run key conflict | 不同 payload 必须使用新的顶层 turn ID |
| AfterRun 较慢 | 当前等待有界 receipt 并有本地 outbox；只有跨主机削峰/持续后台投递才引入 queue |
| 日志出现正文 | 关闭 `MEMORY_MCP_LOG_CONTENT` 并清理内容日志 |
