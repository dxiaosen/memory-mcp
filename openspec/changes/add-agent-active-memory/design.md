## Context

Memory MCP 当前已有三层 Hook 基础设施：

1. `MemoryMcpClient` 负责认证后的 Streamable HTTP MCP 调用；
2. `MemoryHookBridge` 负责 BeforeRun 召回、AfterRun 捕获、fail-open、重试和稳定
   event ID；
3. `HookedAgentRunner` 展示框架无关的单进程接线方式。

缺口位于 Agent Host 边界。Codex 和 Claude Code 都以独立命令进程执行
`UserPromptSubmit` 与 `Stop` Hook，并通过 stdin/stdout 交换 JSON。Before 和
After 不共享 Python 内存，因此现有 Bridge 的进程内缓存不能保存原始用户输入。

首批两个宿主的生命周期语义相同，但轮次字段不同：

| 语义 | Codex | Claude Code |
| --- | --- | --- |
| 会话标识 | `session_id` | `session_id` |
| 轮次标识 | `turn_id` | `prompt_id` |
| Before 输入 | `UserPromptSubmit.prompt` | `UserPromptSubmit.prompt` |
| After 输出 | `Stop.last_assistant_message` | `Stop.last_assistant_message` |
| 上下文注入 | `hookSpecificOutput.additionalContext` | 同左 |

Codex 当前只执行 command 类型 Hook；Claude Code 虽支持直接 `mcp_tool` Hook，但
原始 `recall_memory` 返回业务回执而不是 Hook 输出格式，且 `Stop` 不包含原始用户
输入。因此统一 command 适配器是两个宿主都能可靠执行的最小公共方案。
其他 Agent 不应进入 Bridge/Core 分支；它们只需把自己的生命周期字段映射成通用
顶层轮次事件，并把通用结果渲染成宿主接受的输出。

## Goals / Non-Goals

**Goals:**

- 一个 `memory-mcp-hook` 命令处理通用顶层轮次合同，并自动归一化首批两个宿主的
  command Hook 字段。
- Hook Client 作为独立轻量发行物安装，不把数据库、模型或 Server 运行依赖带到
  Agent Host。
- 宿主输入解析、主动记忆执行和宿主输出渲染彼此分离，使后续 Agent 只新增薄映射。
- Agent 使用者只必须提供 `MEMORY_MCP_URL` 与 `MEMORY_MCP_TOKEN`。
- Before 阶段在模型请求前完成召回和安全上下文注入。
- After 阶段在本轮最终回复产生后提交一次完整、幂等的捕获事件。
- 复用现有 MCP Client、Bridge、认证与 Core，不复制业务规则。
- 提供宿主配置、版本要求、手工验证和故障排查文档。

**Non-Goals:**

- MCP 协议本身不能远程安装宿主 Hook；用户仍需进行一次静态 Hook 注册。
- 不自动捕获 `SubagentStop`，子 Agent 仍属于顶层轮次的内部步骤。
- 不引入消息队列、后台 worker、数据库表或 migration。
- 不解析不稳定的 Codex/Claude transcript 格式。
- 不支持会继续主轮次的其他 `Stop` Hook 与本 Hook 之间的跨 Hook 协调。
- 不把 `profile_id` 变成按提示分类的模型推断结果。

## Decisions

### 1. 使用单一命令和通用生命周期合同

新增 `memory-mcp-hook` console script。命令只读取 stdin 中的
`hook_event_name`，不要求 `before`/`after` CLI 参数。宿主输入先归一化为：

```text
AgentTurnEvent
├── phase = before_run | after_run
├── conversation_id
├── turn_id
├── cwd
├── user_input?
└── final_output?
```

主动记忆执行返回宿主无关的
`AgentHookOutcome(additional_context?, warning_code?)`，最后由 command Hook JSON
renderer 生成 stdout：

```text
UserPromptSubmit → save turn state → MemoryHookBridge.before_run → JSON context
Stop             → load turn state → MemoryHookBridge.after_run_success → {}
```

Codex `turn_id`、Claude Code `prompt_id` 和通用 `run_id` 都可归一化；标准
`BeforeRun`/`AfterRun` 事件允许其他 command Hook Host 或包装脚本直接接入。
出现多个不同标识时拒绝含糊输入。相同命令可直接写入 Codex 和 Claude Code
配置，减少宿主特有脚本及文档分叉。

**拒绝方案：**

- 为每个宿主复制两套脚本：会让失败语义、状态格式和后续修复发生漂移。
- 在 Adapter 内根据宿主名称分支业务行为：新增 Agent 会持续修改核心适配逻辑。
- 直接配置 Claude `mcp_tool`：Codex 当前不执行该 Hook 类型，而且现有工具输出与
  Hook JSON 不同。
- 解析 transcript：两个官方文档都不把 transcript JSON 当成稳定接口，并且
  Stop 已直接提供最终助手文本。

### 2. 连接配置只保留两个公开名称

Agent Host 推荐变量为：

```text
MEMORY_MCP_URL
MEMORY_MCP_TOKEN
```

`MemoryHookSettings` 只从这两个公开环境变量读取连接信息。其余参数继续保留代码
默认值，只作为高级调优项，不进入快速开始配置。`profile_id` 固定默认
`general-work`；MCP 工具参数也提供同一默认值。

服务端 `MEMORY_MCP_*` 配置与 Agent Host 在不同进程中加载。服务端 settings
没有 `url` 或 `token` 字段，因此新增的 Agent 变量不会改变服务端行为。

**拒绝方案：**

- 从 Codex/Claude 私有 MCP 配置文件反向读取地址和 Token：格式、作用域和凭据
  存储各不相同，会制造高风险的隐式 Secret 读取。
- 将 Token 放入 Hook 命令参数：会进入进程列表、配置文本和 shell 历史。

### 3. 使用短期本地状态关联两个命令进程

`UserPromptSubmit` 把以下最小数据保存在事件 `cwd` 下：

```text
.memory-mcp/hooks/<sha256(session_id + turn_id)>.json
```

内容只包含 schema version、`session_id`、规范 `turn_id`、原始 prompt 和创建时间。
目录权限为 `0700`，文件权限为 `0600`；使用同目录临时文件和 `os.replace` 原子写入。
文件名只使用摘要，不接受 Hook 输入作为路径。`Stop` 校验 payload 标识后读取，
捕获尝试完成后删除。每次 Before 还会清理超过固定 TTL 的遗留状态。

状态写入发生在召回之前，因此即使 recall fail-open，本轮仍可在 Stop 捕获。状态
内容不写入 operational log；日志只记录宿主、会话/轮次摘要、阶段、数量、状态和
错误码。

**拒绝方案：**

- 依赖 `MemoryHookBridge` 内存缓存：两个 Hook 是不同进程。
- 外部队列：当前单轮状态很小，服务端已提供幂等与捕获重试，队列会引入新的
  部署、身份和故障边界。
- 把 prompt 编码进 stdout 或环境变量：会污染模型上下文或暴露给子进程。

### 4. 复用现有 Bridge 作为行为边界

通用 Adapter 不读取宿主原始字段，也不直接拼 MCP payload。Before 构造：

```text
HookContext(
  conversation_id=session_id,
  turn_id=turn_id 或 prompt_id,
  profile_id=general-work
)
```

然后调用 `MemoryHookBridge`。After 用同一 Context、已保存 prompt 和
`last_assistant_message` 调用 Bridge。Bridge 继续生成稳定 event ID，并执行现有
有界重试和 fail-open。服务端事务边界仍是 `capture_completed_turn` 内部的
PostgreSQL 原子提交；本地状态不参与服务端事务。

### 5. Before 同步，After 也使用同步公共基线

Before 必须同步，因为召回结果要进入当前模型请求。Claude Code 支持 async
command Hook，但 Codex 当前解析 `async` 而不执行异步命令；为保证一致、便于首轮
联调和明确观察 capture 结果，两个宿主的推荐 Stop 配置都同步等待。

如果未来两个宿主均提供可靠异步完成语义，可以只调整宿主配置，不改变 MCP
合同。但异步进程退出前仍必须完成 MCP 调用，不能只创建未等待的 coroutine。

### 6. 只处理顶层成功轮次

适配器仅接受 `UserPromptSubmit` 与 `Stop`。不注册 `SubagentStart`、
`SubagentStop`、`PreToolUse`、`PostToolUse` 或 `SessionEnd`：

- 工具和子 Agent 步骤不会形成独立长期记忆捕获；
- `Stop` 表示一个用户轮次结束，而不是整个会话关闭；
- 中断或未产生 `last_assistant_message` 的轮次不会捕获。

如果同一宿主还配置了会阻止 Stop 并要求 Agent 继续的其他 Hook，第一次 Stop 时
无法知道并发 Hook 的最终决定。文档将其列为限制，推荐不要在同一轮次组合此类
继续型 Stop Hook。

### 7. 输出严格遵循宿主公共 JSON

Before 有记忆时输出：

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "<server-rendered-memory-context>"
  }
}
```

无记忆或 After 成功时输出 `{}`。stdout 只包含一个 JSON 对象；运行日志写入
stderr/日志文件。配置错误与非 fail-open 错误由 CLI 转换为不含内容或 Secret 的
`systemMessage`，但默认不阻止 Agent 主任务。

### 8. Server 与 Agent 使用两个发行包

仓库使用一个 uv workspace 管理两个独立 Python distribution：

```text
server/
└── memory-mcp              # memory_mcp：Core、transport、storage、extraction

agent/
└── memory-mcp-agent        # memory_mcp_agent：client、bridge、hosts、state、cli
```

`memory-mcp-agent` 只依赖 HTTP Client、Pydantic 和 Pydantic Settings，并独立
提供 `memory-mcp-hook`。它内置仅覆盖 `initialize`、`notifications/initialized`
与 `tools/call` 的最小 MCP Streamable HTTP JSON-RPC 客户端；该客户端和 Server
固定启用的 JSON response 模式一起做真实 HTTP 集成测试，不引入包含 ASGI Server、
OAuth/JWT 等服务端能力的完整 MCP SDK。它不得依赖或导入
LangChain、模型 Provider、psycopg、Server settings、数据库 migration 或 Memory
Core。Server 发行包不再提供 Hook CLI，也不把 Agent 包作为生产依赖。

不发布的根 virtual workspace 通过开发依赖引用两个 member，使统一测试仍可覆盖 Server 与 Agent
真实 HTTP/MCP 集成。发布和部署必须按发行物选择：

- Server：安装 `memory-mcp`；
- Agent Host：只安装 `memory-mcp-agent`；
- 开发仓库：`uv sync --all-packages` 同步两个 member。

选择独立 distribution 而不是 root package extra，是因为 extra 仍会把服务端模块
和无效的 Server console scripts 安装到 Agent 环境，并容易让操作者误以为 Agent
需要数据库配置。选择 Python 包而不是立即冻结单文件二进制，是为了保留跨平台
安装、依赖安全更新和可调试性；后续可以在不改变 CLI 合同的前提下额外发布二进制
或宿主插件。

## Risks / Trade-offs

- **[本地状态短暂包含用户 prompt]** → 权限收紧、摘要文件名、原子写入、成功后
  删除和 TTL 清理；不写日志。
- **[进程在 Stop 前崩溃留下状态]** → 下一次 Before 自动清理过期文件。
- **[Hook 开启于会话中途导致 Stop 找不到状态]** → fail-open、记录稳定错误码，
  不解析 transcript，也不提交不完整捕获。
- **[旧版宿主缺少稳定轮次 ID]** → 明确最低版本；缺少 `turn_id`/`prompt_id`
  时 fail-open，不使用易碰撞的 prompt hash 伪造轮次。
- **[Before 网络延迟增加首 token 时间]** → 15 秒客户端上限、宿主超时和
  fail-open；后续可在不改变合同的前提下优化服务端检索。
- **[其他 Stop Hook 继续当前轮次]** → 文档声明限制；本 Hook 本身永不返回
  block/continue 决策。
- **[旧连接变量残留造成误配置]** → 配置校验拒绝缺少公开 URL/Token 的进程，测试
  验证旧变量不能单独建立连接，日志不报告 Secret 值。
- **[workspace 拆包后 import 或发布漂移]** → Agent 单元测试从独立包导入，构建
  wheel 后在隔离环境检查依赖元数据、console script 和禁止的服务端模块。

## Migration Plan

1. 先发布 Server 包，再构建并发布独立 `memory-mcp-agent` 包。
2. Agent 进程设置 `MEMORY_MCP_URL` 和 `MEMORY_MCP_TOKEN`，并在升级前迁移旧的
   Hook 连接变量。
3. Agent Host 只安装 `memory-mcp-agent`，注册其提供的相同命令并检查
   Hook 信任/加载状态。
4. 先用一个测试 owner 完成“偏好声明 → 下一轮召回”闭环，再逐步启用其他用户。
5. 回滚只需移除/禁用宿主 Hook；手工 MCP 工具和服务端数据保持可用。

## Open Questions

无阻塞问题。跨宿主插件式自动安装、单文件二进制和可靠异步 After 属于后续独立
变更。
