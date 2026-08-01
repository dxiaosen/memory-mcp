# Agent 主动记忆接入

本文说明如何让任意 Agent 在顶层轮次开始前自动召回、在成功结束后自动捕获。
Memory MCP Server、PostgreSQL 和模型抽取部署见[部署指南](deploy.md)，全部配置字段
见[配置参考](config.md)。

Codex 和 Claude Code 是当前提供可复制配置的首批宿主，不是实现边界。其他 Agent
只要能提供稳定的会话/轮次生命周期，就可以使用同一个命令或通用 Python API。
Hook 命令来自独立的 `memory-mcp-agent` 发行包，不要求 Agent Host 安装 Server、
PostgreSQL、模型 Provider 或数据库 migration。

## 1. 连接 MCP 不等于主动调用

只配置 MCP 连接后，Agent 可以发现 `recall_memory` 和
`capture_completed_turn`，但是否调用仍由模型判断。主动记忆额外依赖宿主 Hook：

```text
用户提交顶层输入
  │
  └─ BeforeRun
       ├─ 保存最小轮次状态
       ├─ recall_memory
       └─ 把 additional_context 注入当前模型请求

Agent 执行模型、工具和子 Agent
  │
  └─ AfterRun（顶层最终回复已经形成）
       ├─ 读取本轮原始输入
       ├─ capture_completed_turn
       └─ 删除最小轮次状态
```

AfterRun 是每个顶层用户轮次结束，不是整个会话关闭。工具调用和子 Agent 不会单独
捕获；用户中断或没有最终回复的轮次也不会提交成功捕获。

实现分为三个边界：

1. 宿主 JSON 归一化为 `AgentTurnEvent`；
2. `AgentHookAdapter` 只处理通用 BeforeRun/AfterRun；
3. `AgentHookOutcome` 再渲染成宿主接受的输出。

因此新增宿主不需要修改 MCP Client、Bridge、状态存储或 Memory Core，也不通过
`turn_id`/`prompt_id` 猜测宿主类型。

## 2. 普通使用者只配置两个运行值

Agent 进程只需要：

```dotenv
MEMORY_MCP_URL=https://memory.example.com/mcp
MEMORY_MCP_TOKEN=<该 Agent Host 自己的 Bearer Token>
```

本地可以复制模板：

```bash
cp agent/.env.example examples/agent.env
chmod 600 examples/agent.env
set -a
source examples/agent.env
set +a
```

然后从同一个终端启动 Agent。IDE、桌面应用或 systemd 进程如果不继承该终端环境，
必须通过自身的 Secret/环境注入机制提供同名变量。

Token 必须是 Memory MCP Server `MEMORY_MCP_AUTH_TOKENS` 中的一枚 key。owner
完全由服务端 Token 映射的 `tenant_id + subject_id` 派生，Agent 侧不配置 owner、
client_id 或 agent_id。同一用户的不同 Agent 可以使用不同 Token，只要服务端把
它们映射到同一 tenant/subject；不同用户必须映射到不同 subject。

以下值是内部默认值，不属于普通用户配置：

| 项目 | 默认值 |
| --- | --- |
| `profile_id` | `general-work` |
| MCP 超时 | 15 秒 |
| fail-open | 开启 |
| recall | 最多 5 条、600 token |
| capture | 最多 3 次有界尝试 |
| 本地状态 | `<事件 cwd>/.memory-mcp/hooks/` |
| 状态 TTL | 24 小时 |

`general-work` 是通用 Hook 的代码默认值。投研产品集成应在封装层固定
`HookContext(profile_id="investment-research", ...)`，终端用户仍只接触 URL 和
Token；Server 和 Hook 都不会根据对话正文猜测场景。仓库手工联调时可以临时设置
`MEMORY_HOOK_PROFILE_ID=investment-research`，它属于集成调试，不应做成每轮用户
选择项。

投研关系能力不会增加 Agent 环境变量。AfterRun 仍只提交一次完成轮次，关系识别在
Server 的 Capture 流程内自动发生：先完成候选准入，再从本轮 auto-save 和同 owner/
Profile 的有效记忆中选择有界端点，最后只保存显式、高置信且符合 Profile 方向的
关系。关系原文必须命中用户消息；Assistant/Tool 自己得出的关系、pending、blocked
和歧义关系不会自动建边。`link_memories` 继续作为治理和修正工具，不是普通用户
必须调用的步骤。自动边绑定当时两端 revision，后续 replacement 会在 Server 事务内
把旧边转为 stale，不需要 Agent 补发关系生命周期事件；普通用户侧仍只配置 URL 和
Token。

### 配置职责不要混合

一个完整接入包含三类配置，它们不是同一个文件：

| 配置 | 所在进程 | 作用 |
| --- | --- | --- |
| Server `.env` | Memory MCP Server | 数据库、模型、Token 到身份的映射 |
| Agent MCP 连接 | Agent Host | 让模型可以发现和手工调用 MCP 工具 |
| Agent Hook 注册 | Agent Host | 保证每个顶层轮次确定性调用主动记忆 |

Agent Host 的 URL/Token 应同时供 MCP 连接和 Hook 进程使用，但不要把服务端 `.env`
复制到 Agent，也不要用 Hook JSON 覆盖原有 MCP 配置。已有 `hooks` 时合并两个事件
项；已有同名 Memory Hook 时只保留一份，避免重复召回和捕获。

“只配置地址和 Token”指运行值只有两个；MCP 协议不能远程改写宿主本地配置，所以
每种 Host 仍需一次性安装轻量 Agent 包并完成静态 Hook 注册。

## 3. 安装 Hook 命令

### 3.1 正式 Agent Host

推荐把 Agent wheel 作为版本化发布制品交付，然后用 `uv tool` 建立独立工具环境：

```bash
uv tool install /path/to/memory_mcp_agent-0.1.0-py3-none-any.whl
command -v memory-mcp-hook
```

如果包已发布到组织 Python registry，可以把 wheel 路径换成固定版本
`memory-mcp-agent==0.1.0`。不要在生产命令中省略版本。

当前仓库构建 wheel：

```bash
uv build --package memory-mcp-agent --wheel
```

从源码 checkout 安装也只指向 Agent 子包：

```bash
uv tool install ./agent
```

以上方式都不会安装根发行包 `memory-mcp`。Agent Host 只得到：

- `memory-mcp-hook`；
- `memory_mcp_agent` Python API；
- HTTP、配置和数据校验所需的轻量依赖。

不会得到 `memory-mcp`、`memory-mcp-db`、PostgreSQL driver、LangChain、模型
Provider、ASGI Server 或数据库配置。Agent 包要求 Python 3.11+，Server 的
Python 3.14 要求不会传递到 Agent Host。

### 3.2 仓库开发环境

统一开发和全量测试需要两个 workspace member：

```bash
uv sync --all-packages --frozen
test -x .venv/bin/memory-mcp-hook
```

这里共享 `.venv` 只是开发便利，不是生产部署拓扑。

配置推荐使用 `command -v memory-mcp-hook` 返回的绝对路径。示例为便于复制写成
`memory-mcp-hook`，要求命令位于 Agent 进程的 `PATH`。

先确认 stdout 只有 JSON：

```bash
printf '%s' \
  '{"session_id":"check","turn_id":"check","cwd":"/tmp","hook_event_name":"SubagentStop"}' \
  | memory-mcp-hook
```

预期：

```json
{}
```

## 4. 通用 Agent 合同

### 4.1 直接使用 command Hook

如果宿主支持“事件 JSON 写入 stdin，命令 JSON 从 stdout 返回”，可以直接映射到
以下标准输入。

BeforeRun：

```json
{
  "hook_event_name": "BeforeRun",
  "conversation_id": "conversation-42",
  "run_id": "turn-7",
  "cwd": "/absolute/project/path",
  "user_input": "本轮用户原始输入"
}
```

AfterRun：

```json
{
  "hook_event_name": "AfterRun",
  "conversation_id": "conversation-42",
  "run_id": "turn-7",
  "cwd": "/absolute/project/path",
  "final_output": "本轮最终助手回复"
}
```

字段要求：

| 字段 | 要求 |
| --- | --- |
| `conversation_id` | 同一会话稳定，非空 |
| `run_id` | 每个顶层轮次唯一，Before/After 完全相同 |
| `cwd` | 可信的绝对工作目录，Before/After 完全相同 |
| `user_input` | BeforeRun 必需，必须是当前顶层用户输入 |
| `final_output` | AfterRun 必需，必须是已经成功形成的最终回复 |

命令也直接兼容首批宿主字段：

| 通用语义 | Codex | Claude Code |
| --- | --- | --- |
| Before/After | `UserPromptSubmit` / `Stop` | 同左 |
| 会话 | `session_id` | `session_id` |
| 轮次 | `turn_id` | `prompt_id` |
| 用户输入 | `prompt` | `prompt` |
| 最终回复 | `last_assistant_message` | 同左 |

多个别名可以同时出现，但值必须一致；不同值会以稳定错误码 fail-open。未知事件和
`SubagentStop` 返回 `{}` 且无副作用。

当前内置 command renderer 返回：

- 有召回内容：`hookSpecificOutput.additionalContext`；
- 无召回或捕获成功：`{}`；
- 可见但不阻止主流程的诊断：`systemMessage`。

如果第三方宿主接受这组 JSON，可以直接执行 `memory-mcp-hook`；如果输出协议不同，
只需写一个薄 renderer，把公开的 `AgentHookOutcome(additional_context,
warning_code)` 映射为宿主格式，主动记忆执行层无需改动。

### 4.2 直接嵌入 Agent Framework

框架自身能在一个进程内包住顶层调用时，优先使用 `HookedAgentRunner`，不需要
command Hook 或跨进程状态文件：

```python
from memory_mcp_agent import (
    HookContext,
    HookedAgentRunner,
    MemoryHookBridge,
    MemoryHookSettings,
    MemoryMcpClient,
)

settings = MemoryHookSettings()
context = HookContext(
    # 通用产品使用 general-work；投研产品在集成代码中固定 investment-research。
    profile_id=settings.profile_id,
    conversation_id=conversation_id,
    turn_id=turn_id,
)

async with MemoryMcpClient(settings) as client:
    bridge = MemoryHookBridge(client, settings)
    result = await HookedAgentRunner(bridge, call_agent).run(
        context,
        user_input,
    )
```

`call_agent(user_input, memory_context)` 应把记忆当作不可信历史上下文，并返回顶层
最终回复。可运行示例见
[`examples/hook_runner.py`](../examples/hook_runner.py)。

### 4.3 新宿主兼容性检查

接入一个新 Agent 前确认：

1. 有模型请求前的顶层 Before 事件；
2. 有最终回复形成后的顶层 After 事件；
3. 两个事件共享稳定会话和轮次标识；
4. Before 能同步注入附加上下文；
5. After 能取得最终助手回复；
6. 工具和子 Agent 事件能与顶层事件区分；
7. command Hook 超时至少覆盖 20 秒 recall、60 秒 capture。

缺少稳定轮次标识时不要用 prompt hash、时间戳或“最新状态”猜测关联；这会在并发
轮次中串数据。该宿主应先增加可靠标识或使用单进程 Runner 集成。

## 5. Codex 配置

Codex 原生支持 `UserPromptSubmit` 和 `Stop` command Hook。官方参考：
[Codex Hooks](https://learn.chatgpt.com/docs/hooks)。

Codex 当前只执行 `type: "command"` 的 Hook；`async` 虽可被解析但尚不执行，
也没有原生 HTTP Hook。因此即使 Memory MCP 在另一台服务器，Codex 所在机器仍
需要安装轻量 `memory-mcp-agent` 命令。该命令才负责向远端 MCP URL 发起 HTTP
请求，不要求安装 Server 代码。

配置位置二选一：

| 位置 | 范围 |
| --- | --- |
| `~/.codex/hooks.json` | 当前用户的所有项目 |
| `<project>/.codex/hooks.json` | 当前可信项目 |

复制 [Codex Hook 示例](../examples/agents/codex-hooks.json)，或把其中的 `hooks`
对象合并到已有文件。若命令不在 `PATH`，把两个 `command` 替换为绝对路径。

Codex 会审核非托管 command Hook：

1. 启动或回到 Codex；
2. 输入 `/hooks`；
3. 检查来源、事件和完整命令；
4. 信任两个 Hook；
5. 输入 `/mcp`，确认 Memory MCP 仍为 connected。

不要在同一配置层同时维护 `hooks.json` 和 `config.toml` 内联 `[hooks]` 的同一
Hook，否则合并后可能重复执行。Codex 当前不执行异步 command Hook，因此 Before
和 After 都同步等待；Stop 最多等待本次有界 capture 完成。

## 6. Claude Code 配置

Claude Code 配置位置：

| 位置 | 范围 |
| --- | --- |
| `~/.claude/settings.json` | 当前用户的所有项目 |
| `<project>/.claude/settings.json` | 项目共享配置 |
| `<project>/.claude/settings.local.json` | 当前用户的项目本地配置 |

首次手工测试推荐 `.claude/settings.local.json`。复制
[Claude Code 示例](../examples/agents/claude-code-settings.json)，或只合并其中的
`hooks` 对象。若命令不在 `PATH`，替换为绝对路径。

适配器依赖稳定的 `prompt_id`，Claude Code 版本必须不低于 `2.1.196`：

```bash
claude --version
```

进入 Claude Code 后：

1. 输入 `/status`，确认目标 settings 文件已经加载；
2. 输入 `/hooks`，确认两个顶层 Hook 都指向 `memory-mcp-hook`；
3. 确认原有 Memory MCP 连接正常。

官方输入输出和配置位置见
[Claude Code Hooks](https://code.claude.com/docs/en/hooks)。

## 7. 手工端到端验证

先启动 Memory MCP Server，并从启动 Agent 的同一环境确认：

```bash
test -n "$MEMORY_MCP_URL"
test -n "$MEMORY_MCP_TOKEN"
```

### 7.1 第一轮：产生长期记忆

输入：

```text
以后这个项目的架构决策记录统一使用中文，这是长期约定。
```

预期：

1. Before 先召回，首次通常为 0；
2. Agent 正常形成最终回复；
3. After 自动提交 user + assistant 完成轮次；
4. 服务端模型完成候选抽取和 auto-save/pending/discard/blocked 准入。

查看 Agent Host 日志：

```bash
tail -n 50 .memory-mcp/logs/agent-hook.log
```

应出现：

```text
event="agent_hook.recall.completed"
event="agent_hook.capture.completed"
```

### 7.2 第二轮：验证主动召回

开启新轮次：

```text
这个项目的架构决策记录应该使用什么语言？
```

预期 `recalled_count` 大于 0，且 Agent 在没有手工调用 `recall_memory` 的情况下
使用已保存约定。召回内容属于历史数据，当前用户指令和系统策略始终优先。

### 7.3 跨 Agent 与 owner 隔离

用映射到同一 tenant/subject 的另一枚 Token 启动另一个宿主，预期仍能召回。换成
不同 subject 的 Token 执行相同查询，预期 `recalled_count=0`。

### 7.4 投研集成

验证内置投研 Profile 时，在启动宿主的环境中临时增加：

```bash
export MEMORY_HOOK_PROFILE_ID=investment-research
```

先输入一条明确、长期且不含交易指令的研究偏好，再在新轮次询问该偏好。服务端可能
保存为 `research_preference`，也可能因模型置信度进入 pending。`thesis`、
`evidence_claim`、`risk`、`catalyst` 等是服务端抽取语义，不需要用户在正文里说出
类型名。联调结束后删除该环境变量即可恢复 `general-work`。

## 8. 本地状态与日志

command Hook 的 Before 和 After 是两个独立进程。原始用户输入短期保存在：

```text
<事件 cwd>/.memory-mcp/hooks/<identifier-digest>.json
```

- 目录权限 `0700`，文件权限 `0600`；
- 文件名只包含会话/轮次标识的 SHA-256 摘要；
- After 捕获尝试结束后删除；
- 中断遗留状态在后续 Hook 中按 24 小时 TTL 清理；
- 不解析宿主 transcript。

Hook 日志位于：

```text
<Hook 进程 cwd>/.memory-mcp/logs/agent-hook.log
```

日志只记录阶段、稳定引用、数量、状态和错误码，不记录 prompt、回复或 Token。
服务端日志仍由 `MEMORY_MCP_LOG_FILE` 控制。

## 9. 故障排查

| 表现/错误码 | 原因与处理 |
| --- | --- |
| `/hooks` 看不到配置 | 检查文件位置、JSON、项目信任和宿主版本 |
| `command not found` | 使用绝对命令路径，确认 Agent 进程 `PATH` |
| `configuration_error` | Hook 进程缺少 URL 或 Token |
| `missing_conversation_identifier` | 事件缺少稳定会话标识 |
| `missing_turn_identifier` | 宿主版本过旧或事件缺少稳定轮次标识 |
| `missing_turn_state` | Hook 中途启用、Before 未保存，或 Before/After 的 cwd/ID 不同 |
| `recall_memory_mcp_unavailable` | 检查地址、网络、TLS、服务和超时；本轮 fail-open |
| `capture_memory_mcp_unavailable` | 捕获未成功；本轮最终回复不会被撤回 |
| recall 为 0 | 记忆未 auto-save、相关性不足、owner 不同或已非 active/current |

直接检查服务：

```bash
curl --fail "${MEMORY_MCP_URL%/mcp}/health"
```

如果 URL 不是标准 `/mcp` 结尾，使用部署文档中的实际 health 地址。

## 10. 当前限制与回滚

- MCP Server 不能自动修改 Agent 的本地 Hook 配置；
- Codex 当前没有原生 HTTP Hook，跨机接入仍由本地轻量 command Client 转发；
- 不要与会在 Stop 阶段要求同一轮继续执行的其他 Hook 组合；
- 当前不需要队列；如果要求 Host 崩溃后仍保证投递，应另行设计 durable
  outbox/queue worker；
- command renderer 当前直接覆盖 Codex/Claude Code 的公共 JSON，其他输出协议需要
  一个薄映射；核心生命周期和 MCP 合同不变。

回滚只需从宿主配置移除或禁用两个 Hook。Memory MCP 工具、已有记忆和服务端数据
不会被删除，手工工具调用仍可继续使用。
