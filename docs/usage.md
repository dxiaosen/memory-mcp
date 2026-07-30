# Memory MCP 端到端使用

## 1. 固定后端快速闭环

项目要求 Python 3.14。先复制配置，替换 PostgreSQL DSN 和三个演示 Token。
`.env.example` 已包含只匹配“以后项目周报默认用表格”的固定候选。
DSN 的 `sslmode` 必须与实例能力一致；生产和公网数据库链路应启用 SSL 后使用
`sslmode=require`。

```bash
cp .env.example .env
uv sync
uv run memory-mcp-db migrate
uv run memory-mcp
```

健康检查和 MCP 地址默认为：

```text
http://127.0.0.1:8765/health
http://127.0.0.1:8765/mcp
```

服务运行后，在另一个终端执行用户 A / Agent A：

```bash
uv run python examples/memory_agent_a.py \
  --conversation-id demo-agent-a \
  --turn-id demo-agent-a-1 \
  --subject weekly-report \
  --input '以后项目周报默认用表格'
```

输出应显示 `capture_status=completed` 且有一个 `created_memory_ids`。然后执行同一
用户的 Agent B：

```bash
uv run python examples/memory_agent_b.py \
  --conversation-id demo-agent-b \
  --turn-id demo-agent-b-1 \
  --subject weekly-report \
  --input '项目周报 表格'
```

输出应显示 `recalled_count=1`，`memory_context` 包含“项目周报默认使用表格”。

最后用用户 B / Agent B 的独立 profile 验证隔离：

```bash
uv run python examples/memory_hook_runner.py \
  --profile user-b-agent-b \
  --conversation-id demo-user-b \
  --turn-id demo-user-b-1 \
  --subject weekly-report \
  --input '项目周报 表格'
```

预期 `recalled_count=0` 且 `memory_context=null`。每次重跑必须换新的 `turn-id`；
只有验证幂等 replay 时才复用完全相同的 turn 和 payload。

## 2. 切换真实模型

修改 `.env`：

```dotenv
MEMORY_MCP_EXTRACTOR_BACKEND=openai-compatible
CHAT_MODEL_PROVIDER=openai
CHAT_MODEL_NAME=your-model
CHAT_MODEL_API_KEY=your-secret
CHAT_MODEL_BASE_URL=https://api.openai.com/v1
CHAT_MODEL_TEMPERATURE=0
```

使用 OpenAI 官方地址时也可以不设置 `CHAT_MODEL_BASE_URL`。DeepSeek 使用
`CHAT_MODEL_PROVIDER=deepseek` 和相应地址。配置变更后重启服务；缺少必需模型
字段时服务会拒绝启动。

真实模式无需配置 `MEMORY_MCP_FIXED_CANDIDATES_JSON`。输入明确偏好、稳定背景、
进行中事项或决策即可；是否 auto-save 仍由服务端保守准入规则决定，而不是模型
自行决定。

## 3. 接入已有 Agent

把实际 Agent 调用适配成异步 callable：

```python
async def call_agent(
    user_input: str,
    memory_context: str | None,
) -> str:
    # 将 memory_context 作为不可信历史数据加入 Agent 上下文。
    # 返回一次顶层用户任务的最终文本。
    ...
```

然后组装：

```python
settings = MemoryHookSettings.from_profile("agent-a")
async with MemoryMcpClient(settings) as client:
    bridge = MemoryHookBridge(client, settings)
    runner = HookedAgentRunner(bridge, call_agent)

    result = await runner.run(
        HookContext(
            conversation_id=conversation_id,
            turn_id=unique_top_level_turn_id,
            subject=subject,
        ),
        user_input,
    )
```

有原生生命周期 Hook 的 Host 可以直接调用 `bridge.before_run(...)` 和
`bridge.after_run_success(...)`。AfterRun 应在 final response 成功生成后执行
一次；对话下一轮会生成新的 turn id，再执行一组 Hook。整段会话关闭时不需要额外
捕获。

BeforeRun 与 AfterRun 都是非阻塞网络 I/O 的 coroutine，不等于已经进入消息队列。
BeforeRun 必须等待召回结果；默认 Runner 等待 AfterRun receipt。若 Host 在响应
用户后用 `create_task` 调度 AfterRun，必须接受进程退出时任务可能丢失，不能把它
当作可靠队列。当前单进程闭环不需要外部队列。

默认 `fail_open=true`：记忆服务短暂不可用不会阻断 Agent 主任务。调用方应记录
返回的 `warning_code` 并监控，而不是记录异常正文或 Secret。强一致流程可配置
profile 的 `FAIL_OPEN=false`。

## 4. Profile 配置

`MemoryHookSettings.from_profile("agent-a")` 会读取：

```text
MEMORY_AGENT_A_MCP_URL
MEMORY_AGENT_A_BEARER_TOKEN
MEMORY_AGENT_A_SCENARIO
MEMORY_AGENT_A_FAIL_OPEN
MEMORY_AGENT_A_RECALL_MAX_ITEMS
MEMORY_AGENT_A_RECALL_TOKEN_BUDGET
MEMORY_AGENT_A_CAPTURE_MAX_ATTEMPTS
MEMORY_AGENT_A_RUN_CACHE_MAX_ENTRIES
```

`agent-b`、`user-b-agent-b` 等 profile 以相同规则生成独立前缀。不要通过命令行
参数传 Token，也不要在日志中打印 settings 或 MCP 异常正文。

## 5. 常见问题

- `capture_not_configured`：当前组合根已消除此正常路径；若出现，说明调用的是旧
  服务进程或自行注入了未配置的 `MemoryService`。
- `invalid_candidate_output`：真实模型违反 schema/来源原文约束，或固定候选 JSON
  与场景不匹配。
- `not_authorized` / `forbidden`：检查 Hook profile Token 是否存在于服务端
  principal 映射，以及 read/write scope。
- 召回为空：确认写入是 auto-save 而不是 pending，查询 owner、scenario 和
  subject 一致，并使用 active/current 记忆。
- 重复保存：确认一个顶层任务使用稳定且唯一的 turn id，内部步骤不要自行调用
  AfterRun。
- `BeforeRun/AfterRun run key was reused`：相同 scenario/conversation/turn 被
  不同输入或输出复用；请生成新的顶层 `turn_id`，不要绕过冲突保护。
