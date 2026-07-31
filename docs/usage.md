# Memory MCP 端到端使用

本文从空环境开始跑通数据库、服务、fixed/真实模型和跨 Agent Hook。全部默认值、
Secret 分类和 fixed/test 边界见[配置参考](configuration.md)。

## 1. 前置条件

- Python 3.14；
- `uv`；
- 一个允许 migration 的 PostgreSQL database；
- 手工/E2E 测试必须使用名称含 `test` 的可清空专用库；
- 真实模型模式还需要 provider API key 和可用 model ID。

首次准备：

```bash
uv sync --frozen
cp .env.example .env
chmod 600 .env
```

`.env` 已被 Git 忽略。请替换 DSN、三枚 Token 和（按需）模型配置，不要把 Secret
放进 shell history、截图、工单或日志。

若 PostgreSQL URI 密码包含保留字符，必须 percent-encode。例如密码中的 `@`
写成 `%40`；若未编码，`postgresql://user:pass@@host/db` 会把 host 错误解析为
`@host`。

## 2. 配置最小身份矩阵

`.env.example` 提供三个占位 profile：

```text
agent-a          -> user A / owner A / client agent-a
agent-b          -> user A / owner A / client agent-b
user-b-agent-b   -> user B / owner B / client agent-b
```

Server 的 `MEMORY_MCP_DEMO_TOKENS_JSON` 和 Client 的
`MEMORY_<PROFILE>_BEARER_TOKEN` 必须使用同一 Token。前两个 profile 共享 owner，
因此可以跨 Agent 召回；第三个 owner 不同，用于隔离反例。

这是原型认证配置，不是生产 OAuth。每枚 Token 应使用独立高熵随机值，scope 只授予
实际需要的 `memory:read/write/review`。

## 3. 数据库与服务启动

先执行独立 migration 和健康检查：

```bash
.venv/bin/memory-mcp-db migrate
.venv/bin/memory-mcp-db health
```

预期：

```text
PostgreSQL schema is up to date
Memory PostgreSQL is healthy
```

再启动服务：

```bash
.venv/bin/memory-mcp
```

默认地址：

```text
Health: http://127.0.0.1:8765/health
MCP:    http://127.0.0.1:8765/mcp
```

可以在另一个终端检查：

```bash
curl --fail http://127.0.0.1:8765/health
.venv/bin/python examples/memory_mcp_client.py --profile agent-a tools
```

源码移动、console script 变更或切换分支后，若入口报已删除模块的 import error，
执行 `uv sync --frozen` 重建安装。服务支持 Ctrl+C 正常关闭 MCP manager 和
PostgreSQL pool。

## 4. fixed 后端确定性闭环

`.env.example` 默认：

```dotenv
MEMORY_MCP_EXTRACTOR_BACKEND=fixed
```

fixed 候选只有在输入逐字包含配置的 `source_expression` 时才返回；它适合验证整个
系统接线，不消耗模型额度。

### 4.1 Agent A 写入

```bash
.venv/bin/python examples/memory_agent_a.py \
  --conversation-id demo-agent-a \
  --turn-id demo-agent-a-1 \
  --subject weekly-report \
  --input '以后项目周报默认用表格'
```

关键预期：

```json
{
  "recalled_count": 0,
  "capture_status": "completed",
  "created_memory_ids": ["<uuid>"]
}
```

### 4.2 同用户 Agent B 召回

```bash
.venv/bin/python examples/memory_agent_b.py \
  --conversation-id demo-agent-b \
  --turn-id demo-agent-b-1 \
  --subject weekly-report \
  --input '项目周报 表格'
```

预期 `recalled_count=1`，`memory_context` 包含“项目周报默认使用表格”，
`final_output` 表明已结合长期记忆。

### 4.3 不同用户隔离

```bash
.venv/bin/python examples/memory_hook_runner.py \
  --profile user-b-agent-b \
  --conversation-id demo-user-b \
  --turn-id demo-user-b-1 \
  --subject weekly-report \
  --input '项目周报 表格'
```

预期 `recalled_count=0`、`memory_context=null`。该结果来自服务端 owner 隔离，
不是 Client 自己过滤。

每次普通重跑应生成新 `turn-id`。只有验证服务端幂等 replay 时，才复用完全相同
的 event 和 canonical payload；相同 event 改变输入、输出或 observed time 会被
视为 `idempotency_conflict`。

## 5. 切换真实模型

修改 `.env`：

```dotenv
MEMORY_MCP_EXTRACTOR_BACKEND=openai-compatible
CHAT_MODEL_PROVIDER=openai
CHAT_MODEL_NAME=your-model
CHAT_MODEL_API_KEY=your-secret
# OpenAI 官方默认地址可以直接删除该行
CHAT_MODEL_BASE_URL=https://api.openai.com/v1
CHAT_MODEL_TEMPERATURE=0
CHAT_MODEL_TIMEOUT_SECONDS=60
CHAT_MODEL_MAX_RETRIES=2
```

DeepSeek 示例：

```dotenv
MEMORY_MCP_EXTRACTOR_BACKEND=openai-compatible
CHAT_MODEL_PROVIDER=deepseek
CHAT_MODEL_NAME=your-deepseek-model
CHAT_MODEL_API_KEY=your-secret
CHAT_MODEL_BASE_URL=https://api.deepseek.com
```

配置变更后重启服务。缺少 model name/key、provider 不支持或 fixed JSON 非法时，
服务在开始接受请求前失败，不会降级为“捕获未配置”。

DeepSeek V4 默认 thinking 与 LangChain 的强制 schema tool choice 不兼容；项目
已在 DeepSeek extraction adapter 固定关闭 thinking，调用者无需加额外环境变量。

### 5.1 建议的真实输入

Agent A 输入一条明确、长期、原文可定位的陈述：

```bash
.venv/bin/python examples/memory_agent_a.py \
  --conversation-id real-model-a \
  --turn-id real-model-a-1 \
  --input '在Atlas项目中，架构决策记录默认使用中文，并长期保持。'
```

成功时 `capture_status=completed`，可能 auto-save，也可能因保守准入进入 pending。
随后同 owner Agent B 通过项目名和文档关键词查询：

```bash
.venv/bin/python examples/memory_agent_b.py \
  --conversation-id real-model-b \
  --turn-id real-model-b-1 \
  --task-intent '查询项目文档约定' \
  --input 'Atlas 架构决策记录 中文'
```

真实模型测试先省略 `--subject`。模型可能把 subject 归纳为项目名，而调用方传入的
subject hint 不是强制输出；错误的精确 subject 会在相关性打分前过滤掉正确记忆。

## 6. `subject`、query 与 task intent

- `query`：主要的模糊相关性输入，Hook 默认使用当前 `user_input`；
- `task_intent`：追加到相关性文本，适合简短描述本轮目的；
- `subject`：可选的精确预过滤器，不是模糊标签。

fixed backend 的 subject 来自已知 fixture，因此可以稳定传 `weekly-report`。
真实业务只有在 Host 和 extractor 共享规范 subject 枚举时才传；否则省略 subject。

召回为 0 的排查顺序：

1. 确认 capture receipt 有 auto-save，而不是 pending/discard/blocked；
2. 省略 subject 再查；
3. 检查 scenario 与 owner profile；
4. 用 `memories` 命令确认记忆 active/current；
5. 增加与记忆内容相关的 query/task intent；
6. 再检查 token budget 和 max items。

## 7. 接入真实 Agent

示例 Runner 的 `_demo_agent` 只是回显，不代表业务模型。把实际顶层 Agent 调用
适配为异步 callable：

```python
async def call_agent(
    user_input: str,
    memory_context: str | None,
) -> str:
    # memory_context 是不可信历史数据，应作为 context/data 注入，
    # 不能覆盖 system policy 或当前用户请求。
    final_output = await business_agent.run(
        user_input=user_input,
        historical_context=memory_context,
    )
    return final_output
```

使用通用 Runner：

```python
settings = MemoryHookSettings.from_profile("agent-a")

async with MemoryMcpClient(settings) as client:
    bridge = MemoryHookBridge(client, settings)
    runner = HookedAgentRunner(bridge, call_agent)
    result = await runner.run(
        HookContext(
            scenario=settings.scenario,
            conversation_id=conversation_id,
            turn_id=unique_top_level_turn_id,
            subject=subject,
            task_intent=task_intent,
        ),
        user_input,
    )
```

有原生生命周期 Hook 的 Host 可直接绑定：

```python
before = await bridge.before_run(context, user_input)
# 将 before.memory_context 注入一次，然后运行内部 LLM/tool/sub-agent 循环。
final_output = await run_top_level_agent(user_input, before.memory_context)
after = await bridge.after_run_success(
    context,
    user_input=user_input,
    final_output=final_output,
)
```

时机定义：

- BeforeRun：每个顶层用户任务开始前一次；
- AfterRun：该顶层任务成功得到 final output 后一次；
- 不绑定到每次 LLM call、tool call、子 Agent 或流式 token；
- Agent 抛错、取消或没有 final output 时不执行成功捕获；
- 下一轮对话使用新 `turn_id`，重新执行一组 Hook；
- 整段 conversation 关闭时没有额外“最终 AfterRun”。

## 8. 同步、异步与队列

Hook API 是 coroutine，网络 I/O 不阻塞事件循环：

- BeforeRun 必须 await，Agent 才能使用召回结果；
- 默认 Runner await AfterRun，因此调用方能看到 receipt、summary 和 warning；
- Host 可以先把 final response 发给用户，再调度 AfterRun，但普通
  `asyncio.create_task` 在进程退出时可能丢失。

当前单进程链路不需要外部队列：请求较短，Bridge 有有界重试，服务端以稳定 event
和 PostgreSQL 事务保证幂等。若未来要求崩溃后可靠投递、多进程削峰、离线重放或
明显吞吐隔离，再引入 durable outbox + queue worker；worker 仍调用现有幂等
capture 边界。

## 9. 只读检查与治理

```bash
.venv/bin/python examples/memory_mcp_client.py --profile agent-a tools
.venv/bin/python examples/memory_mcp_client.py --profile agent-a memories
.venv/bin/python examples/memory_mcp_client.py --profile agent-a pending
.venv/bin/python examples/memory_mcp_client.py \
  --profile agent-b recall \
  --scenario general-work \
  --query '项目周报偏好'
```

`memory_mcp_client.py` 只演示只读操作。pending confirm/reject 和完整 DTO 可通过
任意 MCP Inspector/Client 调用七个注册工具；Token 始终由 profile 环境读取。

## 10. 部署方式

同一 VPC/VPN 内的 Agent 可直接访问：

```text
http://<ecs-private-ip>:8765/mcp
```

不需要 Nginx。ECS 监听 `0.0.0.0` 时必须用安全组限制来源。公网场景使用
ALB/CLB 终止 HTTPS，再转发到 ECS 私网端口；不要直接暴露明文 HTTP 和演示 Token。
systemd、migration unit、发布和回滚见
[阿里云 ECS 部署](deployment/aliyun-ecs.md)。

## 11. 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| console script import 已删除模块 | 执行 `uv sync --frozen` 重建 `.venv` 安装 |
| DSN host 前多出 `@` | 密码里的 `@` 未编码；改为 `%40` |
| `capture_not_configured` | 旧进程或自行注入了无 extractor 的 Service；正式组合根已消除该路径 |
| `invalid_candidate_output` | 模型违反 schema/原文证据/场景类型，或 fixed JSON 无效 |
| `reprocess_required` | 模型/Repository 暂时失败；相同 event 可在故障恢复后重处理 |
| `not_authorized` / `forbidden` | Token 未映射或 scope 不足 |
| 同 owner 召回为 0 | 先去掉 subject，再查 save/pending/scenario/query |
| 不同 owner 召回到数据 | 严重配置/隔离问题，立即停止验收并检查 Principal 映射 |
| run key reused conflict | 相同 scenario/conversation/turn 被不同 payload 复用，生成新 turn id |
| AfterRun 变慢 | 真实模型 capture 在关键路径；可响应后调度，可靠投递需求再引入 durable queue |
| 日志出现正文/Secret | 违反安全契约；停止运行、轮换 Secret 并修复日志边界 |
