# Memory MCP 端到端使用

本文从空环境开始，跑通数据库、服务端真实模型抽取、Agent Hook、测试注入的
确定性验证以及跨 Agent/跨用户隔离。完整字段和边界见[配置参考](config.md)。

## 1. 先理解两个进程

完整链路至少包含两个独立进程：

```text
Agent Host
  memory-mcp-agent (Python 3.11+)
  MEMORY_MCP_URL + MEMORY_MCP_TOKEN
       │ BeforeRun / AfterRun
       ▼
Memory MCP Server
  memory-mcp (Python 3.14+)
  MEMORY_MCP_DATABASE_*
  MEMORY_MCP_AUTH_*
  MEMORY_MCP_MODEL_*
  MEMORY_MCP_LOG_*
       │
       ▼
PostgreSQL
```

模型候选抽取属于 Server。Agent Host 只知道 MCP 地址和自己的 Token。`profile_id`、
Hook 预算、重试和 owner 都不要求用户配置。多个 Agent Host 使用相同的两个变量
名，但由各自部署环境注入不同值；不存在运行时身份配置选择器。

示例 Runner 中的 `_agent` 只是可替换的接线 callable。它验证 Hook 生命周期，但
不冒充业务 Agent 大模型；真实业务接入方法见第 8 节。

## 2. 前置条件与安装

- Python 3.14；
- `uv`；
- 一个允许 migration 的 PostgreSQL database；
- 破坏性测试使用名称包含 `test` 的专用可清空 database；
- 真实模型 provider 的 API key 和 model ID。

仓库开发环境安装两个 workspace member 的锁定依赖：

```bash
uv sync --all-packages --frozen
```

生产 Server 使用
`uv sync --frozen --no-dev --package memory-mcp`；远端 Agent Host 只安装
`memory-mcp-agent` wheel。不要用仓库开发环境的共享 `.venv` 推断生产两端必须
同机或必须安装同一套依赖。

建立服务端本地配置：

```bash
cp server/.env.example .env
chmod 600 .env
```

编辑 `.env`，替换数据库 DSN、静态 Token、模型名称和 API Key。URI 密码中的
`@`、`:`、`/`、`?`、`#`、`%` 必须 percent-encode。

建立一个 Agent Host 配置：

```bash
cp agent/.env.example examples/agent.env
chmod 600 examples/agent.env
```

编辑 `examples/agent.env`：

```dotenv
MEMORY_MCP_URL=http://127.0.0.1:8765/mcp
MEMORY_MCP_TOKEN=<与服务端映射中完全相同的一枚 Token>
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

从旧 `scenario` 命名版本升级时，第一次命令会显示
`Applied PostgreSQL migrations: 0003_profile_naming.sql`。该 migration 使用表、列
和索引 rename 原地保留记忆数据；执行成功后工具字段统一为 `profile_id`，旧客户端
如果仍提交 `scenario` 会被严格 DTO 拒绝，需要与 Server 一起升级。

首次升级到元数据版本还会显示
`Applied PostgreSQL migrations: 0004_memory_metadata.sql`。它保留旧 revision 和
Evidence：历史 `extraction_confidence` 为 null，`valid_from` 从原 `observed_at`
回填，并增加 verification、sensitivity、validity 与 citation 字段。随后还会应用
`0005_metadata_rollback_compat.sql`，保证保留向前 schema 时旧版 Server 仍能短期
回滚写入。关系版本还会应用 `0006_memory_relations.sql`，增加关系目录、关系表、
同 owner/Profile 端点外键和活动关系唯一索引；随后 `0007_relation_provenance.sql`
增加 revision 快照、自动 provenance 和 stale 生命周期，旧边只标成 `legacy/item`。
显示 `PostgreSQL schema is up to date` 代表七条 migration 都已应用且 checksum 一致，
不是“没有数据库表”。

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
`uv sync --all-packages --frozen` 重建开发安装。服务支持 Ctrl+C 正常关闭 MCP
manager 和数据库池。

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

### 4.1 手工验证投研 Profile

Server 已同时注册 `general-work` 与 `investment-research`，但不会从正文猜测场景。
通用 Agent 默认继续使用前者；投研产品集成应在代码中固定后者。仓库手工验证可只
为测试进程临时覆盖 Profile：

```bash
MEMORY_HOOK_PROFILE_ID=investment-research \
  .venv/bin/python examples/hook_runner.py \
  --env-file examples/agent.env \
  --conversation-id research-a \
  --turn-id research-a-1 \
  --input '我长期要求投研结论同时列出支持证据和反方风险。'
```

新轮次召回：

```bash
MEMORY_HOOK_PROFILE_ID=investment-research \
  .venv/bin/python examples/hook_runner.py \
  --env-file examples/agent.env \
  --conversation-id research-b \
  --turn-id research-b-1 \
  --input '这次投研结论应该采用什么证据结构？'
```

直接 MCP 调用也可以显式传 `profile_id="investment-research"`。八种合法类型为
`research_preference`、`research_question`、`thesis`、`evidence_claim`、`risk`、
`catalyst`、`ongoing_research` 和 `research_decision`。真实模型可能选择 pending；
这属于保守准入，不应为了演示强制 auto-save。

文档、网页或工具来源可以在 `messages[]` 对应消息中提交 `source_type`、URI、标题、
发布者、发布/获取时间、hash 和 locator。它们会跟随 Evidence 返回，但不会自动把
verification 设成 `source_verified`。投研 subject 应细化到实体/主题与指标、期间、
事件或问题焦点；无法保证 canonical subject 时，召回仍应省略 subject。

### 4.2 自动建立和治理投研关系

启用 `investment-research` 后，AfterRun 的 Server Capture 会先完成记忆候选准入，
再从本轮 auto-save 记忆和同 owner/Profile 的有效既有记忆中选择最多 40 个端点，
执行一次独立结构化关系抽取。只有原文明确表达、confidence 不低于 `0.90`、端点
唯一、命中用户消息且符合 Profile 方向的关系才会与本轮记忆在同一个事务自动保存；
Assistant/Tool 自己给出的结论、pending、blocked、低置信、推断或歧义关系不会形成边。

手工端到端验证建议分两轮：第一轮在 `investment-research` 下明确保存一条 thesis；
第二轮提交一条 evidence_claim，并在原文中明确说明“该证据支持前述论点”。第二轮
完成后用 `list_memories` 找到 evidence/thesis，再对任一端点调用 `get_memory`；
`relations[]` 应出现 `supports`、正确的 incoming/outgoing 方向和另一端 memory ID。
默认日志应出现 `memory.capture.relations_planned`，其中 `accepted_count=1`；不需要让
Agent 额外调用关系工具。真实模型若把任一记忆判为 pending，先通过 Review 确认后在
后续轮次再次明确关系，系统不会给 pending 候选建立悬空边。

因此普通 Agent 不需要取得 memory ID 或主动调用关系工具。`link_memories` 仍保留为
人工验收、历史数据补链和错误修正的治理能力。MCP Inspector 可以先查看
`list_memories`，再显式调用：

```text
link_memories {
  "source_memory_id":"<evidence_claim UUID>",
  "target_memory_id":"<thesis UUID>",
  "relation_type":"supports"
}
```

相同 owner/source/target/type 的重复调用返回同一活动关系。投研 Profile 允许
`supports`、`challenges`、`threatens`、`could_catalyze`、`addresses` 和 `resolves`；
合法方向见[配置参考](config.md#7-固定记忆配置)。`get_memory` 默认返回活动一跳
关系，`include_history=true` 还会返回 provenance、stale 和已撤销关系。自动边的
`origin=automatic`、`scope=revision`，会给出两端 revision、capture、来源表达、
confidence 和抽取版本；显式工具边为 `manual/item` 且没有模型 provenance。需要撤销时调用：

```text
revoke_memory_relation {"relation_id":"<UUID>"}
```

关系撤销不删除端点或历史。关系 MCP 工具同样不接受 owner；跨用户 ID 与不存在不可
区分。撤销一条错误边当前仍是显式治理操作；如果任一端点记忆 revoked 或到期，
这条关系会自动停止参与普通详情与 recall，但历史关系行继续保留。自动关系任一端
replacement 时也会转为 `stale/endpoint_revision_changed`；新的 current revision
必须重新建立关系，旧边不会静默跟随新内容。自动关系不改变普通用户配置，仍然只有
URL 和 Token。

### 4.3 自动质量评估

默认离线评估不读取模型配置、网络、Token 或数据库：

```bash
.venv/bin/python -m evals.runner
```

它输出候选/关系 precision、recall、Recall@K、安全负例通过率和失败 case ID，阈值
不满足时返回非零。显式评估当前真实模型时，在已加载 `MEMORY_MCP_MODEL_*` 的环境中
增加 `--live-model`；该模式只使用进程内 Repository，不写生产 PostgreSQL。完整合同
见 `evals/README.md`。

### 4.4 查看元数据、到期和撤销

`list_memories`、`get_memory(include_history=true)` 和 `recall_memory` 会返回
extraction confidence、verification、sensitivity、validity；详情/history 还返回
完整 Evidence citation。到达 `valid_until` 后普通 list/recall 不再返回该项，但
`get_memory` 和 history 仍可审计。

需要立即停用一条 current memory 时，使用 `memory:review` scope 调用：

```text
revoke_memory {"memory_id":"<UUID>"}
```

调用是 owner-scoped 且幂等的：重复调用返回同一 revoked revision，另一 owner 猜中
UUID 时只得到 `memory_unavailable`。revoke 不等于物理删除，Evidence 和 history
继续保留。

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
cp agent/.env.example examples/agent-a.env
cp agent/.env.example examples/agent-b.env
cp agent/.env.example examples/user-b-agent-b.env
chmod 600 examples/agent-a.env examples/agent-b.env examples/user-b-agent-b.env
```

每个文件只填写同名 `MEMORY_MCP_URL` 和 `MEMORY_MCP_TOKEN`，但 Token 分别对应
服务端的三枚 key。不要把三份 Token 合并到同一个 Agent 进程。

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
3. 检查 `profile_id` 和服务端 Token 的 tenant/subject 映射；
4. 用 `memories` 命令确认记忆仍为 active/current；
5. 增加与记忆内容相关的 query/task intent；
6. 检查 max items 和 token budget。

## 8. 接入真实业务 Agent

支持 command Hook 的宿主优先使用 [Agent 主动记忆接入](agents.md) 中的标准
BeforeRun/AfterRun 合同；Codex 和 Claude Code 已提供可直接复制的配置。框架本身
能够包装顶层调用时，可以使用下面的 `HookedAgentRunner`，不需要 command Hook
或跨进程状态文件。

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
from memory_mcp_agent import (
    HookContext,
    HookedAgentRunner,
    MemoryHookBridge,
    MemoryHookSettings,
    MemoryMcpClient,
)

settings = MemoryHookSettings()

async with MemoryMcpClient(settings) as client:
    bridge = MemoryHookBridge(client, settings)
    runner = HookedAgentRunner(bridge, call_agent)
    result = await runner.run(
        HookContext(
            profile_id=settings.profile_id,
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
  --profile-id general-work \
  --query '项目周报偏好'
```

`client.py` 只演示只读操作。pending confirm/reject 和完整 DTO 可通过任意 MCP
Inspector/Client 调用十个注册工具。Token 始终从 Agent 进程环境或其显式 env
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
| 开发环境 console script 引用旧模块 | 执行 `uv sync --all-packages --frozen` 重建 `.venv` |
| Agent Host 找不到 Hook 命令 | 只安装版本化 `memory-mcp-agent` wheel，并把 `uv tool dir --bin` 加入 `PATH` |
| DSN host 前多出 `@` | 密码中的 `@` 未编码；改为 `%40` |
| 服务启动提示 model name/key 缺失 | 生产 backend 是真实模型；补齐 `MEMORY_MCP_MODEL_*` |
| `invalid_candidate_output` | 模型违反 schema、原文 Evidence 或当前 profile 类型 |
| `reprocess_required` | 模型或 Repository 暂时失败；故障恢复后可复用相同 event |
| `not_authorized` / `forbidden` | Agent Token 未映射或 scope 不足 |
| 同 owner 召回为 0 | 先去掉 subject，再检查 save/pending/profile_id/query |
| 不同 owner 召回到数据 | 严重隔离问题；立即停止验收并检查 Principal 映射 |
| run key reused conflict | 相同 profile_id/conversation/turn 被不同 payload 复用；生成新 turn ID |
| AfterRun 变慢 | 真实模型 capture 位于关键路径；可响应后调度，可靠投递需求再引入 durable queue |
| 日志出现正文 | 检查 `MEMORY_MCP_LOG_CONTENT`；关闭并清理已有内容日志 |
| 日志出现 Secret 或被拦截原文 | 违反日志契约；停止运行、轮换 Secret 并修复日志边界 |
