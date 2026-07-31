# Memory MCP 端到端使用

本文从空环境开始，跑通数据库、服务端真实模型抽取、Agent Hook、测试注入的
确定性验证以及跨 Agent/跨用户隔离。完整字段和边界见[配置参考](config.md)。

## 1. 先理解两个进程

完整链路至少包含两个独立进程：

```text
Agent Host
  MEMORY_HOOK_*
       │ BeforeRun / AfterRun
       ▼
Memory MCP Server
  MEMORY_MCP_DATABASE_*
  MEMORY_MCP_AUTH_*
  MEMORY_MCP_MODEL_*
  MEMORY_MCP_LOG_*
       │
       ▼
PostgreSQL
```

模型候选抽取属于 Server。Agent Host 只知道 MCP 地址、自己的 Token 和 Hook 参数。
多个 Agent Host 使用相同的 `MEMORY_HOOK_*` 变量名，但由各自部署环境注入不同值；
不存在运行时身份配置选择器。

示例 Runner 中的 `_agent` 只是可替换的接线 callable。它验证 Hook 生命周期，但
不冒充业务 Agent 大模型；真实业务接入方法见第 8 节。

## 2. 前置条件与安装

- Python 3.14；
- `uv`；
- 一个允许 migration 的 PostgreSQL database；
- 破坏性测试使用名称包含 `test` 的专用可清空 database；
- 真实模型 provider 的 API key 和 model ID。

安装锁定依赖：

```bash
uv sync --frozen
```

建立服务端本地配置：

```bash
cp .env.example .env
chmod 600 .env
```

编辑 `.env`，替换数据库 DSN、静态 Token、模型名称和 API Key。URI 密码中的
`@`、`:`、`/`、`?`、`#`、`%` 必须 percent-encode。

建立一个 Agent Host 配置：

```bash
cp examples/.env.example examples/agent.env
chmod 600 examples/agent.env
```

编辑 `examples/agent.env`：

```dotenv
MEMORY_HOOK_MCP_URL=http://127.0.0.1:8765/mcp
MEMORY_HOOK_BEARER_TOKEN=<与服务端映射中完全相同的一枚 Token>
```

Token 至少 32 字符。不要把真实 DSN、Token 或 API Key 放入命令行、Git、截图或
日志。生产部署应使用平台 Secret，而不是保留这些本地文件。

## 3. 数据库与服务启动

先执行独立 migration 和数据库健康检查：

```bash
.venv/bin/memory-mcp-db migrate
.venv/bin/memory-mcp-db health
```

预期：

```text
PostgreSQL schema is up to date
Memory PostgreSQL is healthy
```

启动服务：

```bash
.venv/bin/memory-mcp
```

默认地址：

```text
Health: http://127.0.0.1:8765/health
MCP:    http://127.0.0.1:8765/mcp
```

另一个终端验证健康和 MCP 工具发现：

```bash
curl --fail http://127.0.0.1:8765/health
.venv/bin/python examples/client.py \
  --env-file examples/agent.env \
  tools
```

源码移动、console script 变更或切换分支后，如果入口仍引用旧模块，执行
`uv sync --frozen` 重建安装。服务支持 Ctrl+C 正常关闭 MCP manager 和数据库池。

## 4. 生产形态：真实模型闭环

根目录模板默认使用真实抽取。OpenAI-compatible 示例：

```dotenv
MEMORY_MCP_MODEL_PROVIDER=openai
MEMORY_MCP_MODEL_NAME=<可用模型 ID>
MEMORY_MCP_MODEL_API_KEY=<Secret>
MEMORY_MCP_MODEL_BASE_URL=https://api.openai.com/v1
MEMORY_MCP_MODEL_TEMPERATURE=0
MEMORY_MCP_MODEL_TIMEOUT_SECONDS=60
MEMORY_MCP_MODEL_MAX_RETRIES=2
```

DeepSeek 示例：

```dotenv
MEMORY_MCP_MODEL_PROVIDER=deepseek
MEMORY_MCP_MODEL_NAME=<可用 DeepSeek 模型 ID>
MEMORY_MCP_MODEL_API_KEY=<Secret>
MEMORY_MCP_MODEL_BASE_URL=https://api.deepseek.com
```

修改后重启服务。model、API key 或 provider 无效时，服务会在启动或 provider
边界失败，不会自动降级为测试 extractor。DeepSeek adapter 会关闭与强制 schema
tool choice 不兼容的默认 thinking。

运行一个带长期信息的顶层 Agent 轮次：

```bash
.venv/bin/python examples/hook_runner.py \
  --env-file examples/agent.env \
  --conversation-id real-model-a \
  --turn-id real-model-a-1 \
  --input '在Atlas项目中，架构决策记录默认使用中文，并长期保持。'
```

重点检查：

- `capture_status` 为 `completed`；
- 候选可能 auto-save，也可能因保守准入进入 pending；
- `created_memory_ids` 非空表示已经 auto-save；
- `capture_warning` 为空。

再运行一个新的顶层轮次进行召回：

```bash
.venv/bin/python examples/hook_runner.py \
  --env-file examples/agent.env \
  --conversation-id real-model-b \
  --turn-id real-model-b-1 \
  --task-intent '查询项目文档约定' \
  --input 'Atlas 架构决策记录 中文'
```

真实模型 smoke 测试先不要传 `--subject`。`subject` 是精确过滤器，而模型可能把
同一概念归纳成不同规范名；错误 subject 会在相关性计算前过滤掉正确记忆。

## 5. 确定性自动化闭环

生产服务没有 fixed backend 开关。无需模型网络的验证由测试代码构造
`FixedCandidateBackend`，再通过 `candidate_extractor` 组合参数注入服务。候选
fixture 也直接归测试用例所有，不进入 `.env`。

准备名称包含 `test` 的可清空数据库，并仅向测试进程注入：

```bash
MEMORY_MCP_TEST_DATABASE_URL='<专用测试数据库 DSN>' \
  .venv/bin/python -m pytest \
  tests/server/test_postgresql_transport.py::test_postgresql_hook_runner_cross_agent_end_to_end \
  -q
```

该用例验证完整链路：

```text
Hook -> HTTP/MCP -> static auth -> Core -> injected fixed candidate
     -> PostgreSQL -> receipt -> another Agent recall
```

只有候选生成是确定性的，其余 transport、认证、Core、PostgreSQL、Hook、跨 Agent
共享和跨用户隔离均为真实实现。这样既避免测试依赖模型概率和网络，也避免把测试
fixture 误配置到生产服务。

## 6. 三身份共享与隔离验收

正式模板只提供一个 Principal。以下矩阵由验收者在专用环境显式建立：

```text
Agent A        -> user A / owner A / client agent-a
Agent B        -> user A / owner A / client agent-b
User B Agent B -> user B / owner B / client agent-b
```

在服务端 `MEMORY_MCP_AUTH_TOKENS` 中配置三枚不同的高熵 Token。Agent A/B 的
`tenant_id` 与 `subject_id` 相同，User B 使用不同 `subject_id`。owner 由服务端
自动派生，无需配置。然后分别复制三个文件：

```bash
cp examples/.env.example examples/agent-a.env
cp examples/.env.example examples/agent-b.env
cp examples/.env.example examples/user-b-agent-b.env
chmod 600 examples/agent-a.env examples/agent-b.env examples/user-b-agent-b.env
```

每个文件只填写同名 `MEMORY_HOOK_*`，但 `MEMORY_HOOK_BEARER_TOKEN` 分别对应服务端
的三枚 Token。不要把三份 Token 合并到同一个 Agent 进程。

用 Agent A 按第 4 节执行一次真实模型写入，再用同 owner Agent B 召回：

```bash
.venv/bin/python examples/hook_runner.py \
  --env-file examples/agent-b.env \
  --conversation-id shared-owner-read \
  --turn-id shared-owner-read-1 \
  --subject weekly-report \
  --input '项目周报 表格'
```

预期 `recalled_count=1`。不同 owner 使用完全相同查询：

```bash
.venv/bin/python examples/hook_runner.py \
  --env-file examples/user-b-agent-b.env \
  --conversation-id isolated-owner-read \
  --turn-id isolated-owner-read-1 \
  --subject weekly-report \
  --input '项目周报 表格'
```

预期 `recalled_count=0`、`memory_context=null`。隔离发生在服务端 Repository
查询边界，不是 Agent Client 自行过滤。

## 7. query、task intent 与 subject

- `query`：主要模糊相关性输入；Hook 默认使用当前 `user_input`；
- `task_intent`：补充本轮目的，参与相关性文本；
- `subject`：可选精确预过滤，不是模糊标签。

确定性测试候选的 subject 已知，可以传完全一致的 `weekly-report`。真实业务只有
在 Host 和 extractor 共享规范 subject 枚举时才应传 subject，否则省略。

召回为 0 的排查顺序：

1. 检查 capture receipt 是否 auto-save，而非 pending/discard/blocked；
2. 省略 subject 再查询；
3. 检查 scenario 和服务端 Token 的 tenant/subject 映射；
4. 用 `memories` 命令确认记忆仍为 active/current；
5. 增加与记忆内容相关的 query/task intent；
6. 检查 max items 和 token budget。

## 8. 接入真实业务 Agent

把示例 `_agent` 替换为业务 Agent 的顶层异步调用：

```python
async def call_agent(
    user_input: str,
    memory_context: str | None,
) -> str:
    # memory_context 是不可信历史数据，只能作为 context/data 注入，
    # 不能覆盖 system policy 或当前用户请求。
    return await business_agent.run(
        user_input=user_input,
        historical_context=memory_context,
    )
```

一个 Agent 进程从自己的环境加载配置：

```python
settings = MemoryHookSettings()

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

有原生生命周期 Hook 的 Host 可以直接绑定：

```python
before = await bridge.before_run(context, user_input)
final_output = await run_top_level_agent(user_input, before.memory_context)
after = await bridge.after_run_success(
    context,
    user_input=user_input,
    final_output=final_output,
)
```

时机定义：

- BeforeRun：每个顶层用户任务开始前执行一次；
- AfterRun：该顶层任务成功得到 final output 后执行一次；
- 不绑定到每次模型调用、工具调用、子 Agent 或流式 token；
- Agent 抛错、取消或没有 final output 时不执行成功捕获；
- 下一轮对话使用新 `turn_id`，重新执行一组 Hook；
- conversation 关闭时没有额外的“最终 AfterRun”。

## 9. 异步、重试与队列

Hook API 是 coroutine，网络 I/O 不阻塞事件循环：

- BeforeRun 必须 await，业务 Agent 才能使用召回结果；
- 默认 Runner await AfterRun，调用方可以检查 receipt、summary 和 warning；
- Host 可以先把 final response 发给用户，再调度 AfterRun，但普通
  `asyncio.create_task` 在进程退出时可能丢失。

当前链路没有引入外部队列。理由是 capture 请求有界、Bridge 有有界重试，Server
通过稳定 event ID 和 PostgreSQL 事务保证幂等。以下需求出现时再增加
durable outbox + queue worker：

- Agent 进程崩溃后仍必须投递；
- 多进程削峰或模型抽取与交互流量隔离；
- 离线重放、死信处理或可观察的投递 SLA；
- AfterRun 延迟已经影响用户体验。

worker 仍应调用现有幂等 capture 边界，队列不能取代服务端幂等与 owner 校验。

## 10. 日志联调

需要从服务端日志观察核心流程时，在受控环境临时设置：

```dotenv
MEMORY_MCP_LOG_LEVEL=INFO
MEMORY_MCP_LOG_CONTENT=true
```

重启后会记录经过日志清洗的输入、候选、准入、持久化、召回排序和最终召回内容。
联调结束后恢复为 `false`，并按数据管理要求清理内容日志。Token、DSN、模型 API
Key、provider 异常正文和敏感规则拦截的原文在任何模式下都不应记录。

## 11. 只读检查

```bash
.venv/bin/python examples/client.py \
  --env-file examples/agent.env \
  tools

.venv/bin/python examples/client.py \
  --env-file examples/agent.env \
  memories

.venv/bin/python examples/client.py \
  --env-file examples/agent.env \
  pending

.venv/bin/python examples/client.py \
  --env-file examples/agent.env \
  recall \
  --scenario general-work \
  --query '项目周报偏好'
```

`client.py` 只演示只读操作。pending confirm/reject 和完整 DTO 可通过任意 MCP
Inspector/Client 调用七个注册工具。Token 始终从 Agent 进程环境或其显式 env
文件读取。

## 12. 部署访问

同一 VPC/VPN 内的 Agent 可以直接访问：

```text
http://<ecs-private-ip>:8765/mcp
```

不需要 Nginx。服务监听 `0.0.0.0` 时必须由安全组限制来源。公网场景使用 ALB/CLB
终止 HTTPS，再转发到 ECS 私网端口；不要直接暴露携带静态 Token 的明文 HTTP。
systemd、migration、发布和回滚见[部署指南](deploy.md)。

## 13. 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| console script import 已删除模块 | 执行 `uv sync --frozen` 重建 `.venv` 安装 |
| DSN host 前多出 `@` | 密码中的 `@` 未编码；改为 `%40` |
| 服务启动提示 model name/key 缺失 | 生产 backend 是真实模型；补齐 `MEMORY_MCP_MODEL_*` |
| `invalid_candidate_output` | 模型违反 schema、原文 Evidence 或场景类型 |
| `reprocess_required` | 模型或 Repository 暂时失败；故障恢复后可复用相同 event |
| `not_authorized` / `forbidden` | Agent Token 未映射或 scope 不足 |
| 同 owner 召回为 0 | 先去掉 subject，再检查 save/pending/scenario/query |
| 不同 owner 召回到数据 | 严重隔离问题；立即停止验收并检查 Principal 映射 |
| run key reused conflict | 相同 scenario/conversation/turn 被不同 payload 复用；生成新 turn ID |
| AfterRun 变慢 | 真实模型 capture 位于关键路径；可响应后调度，可靠投递需求再引入 durable queue |
| 日志出现正文 | 检查 `MEMORY_MCP_LOG_CONTENT`；关闭并清理已有内容日志 |
| 日志出现 Secret 或被拦截原文 | 违反日志契约；停止运行、轮换 Secret 并修复日志边界 |
