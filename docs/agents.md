# Agent 主动记忆接入

让任意 Agent 在顶层轮次开始前自动召回、成功结束后自动捕获。Hook 命令来自独立的
`memory-mcp-agent` 发行包，不要求 Agent Host 安装 Server、PostgreSQL、模型 Provider
或数据库 migration。部署见[部署指南](deploy.md)，配置字段见[配置参考](config.md)。

Codex 和 Claude Code 是首批提供可复制配置的宿主，不是实现边界。其他 Agent 只要能
提供稳定的会话/轮次生命周期，即可使用同一命令或通用 Python API。

## 1. 连接 MCP ≠ 主动调用

只配置 MCP 连接后，Agent 可发现 `recall_memory` / `capture_completed_turn`，但是否
调用仍由模型判断。主动记忆额外依赖宿主 Hook：

```text
用户提交顶层输入 → BeforeRun：保存最小轮次状态 → recall_memory → 注入 additional_context
  │
  Agent 执行模型、工具和子 Agent
  │
  → AfterRun（顶层最终回复已形成）：读取本轮原始输入 → capture_completed_turn → 删除轮次状态
```

AfterRun 是每个顶层用户轮次结束，不是会话关闭。工具调用和子 Agent 不单独捕获；
用户中断或无最终回复的轮次不提交成功捕获。实现分三个边界，新增宿主无需修改 MCP
Client、Bridge、状态存储或 Memory Core，也不通过 `turn_id`/`prompt_id` 猜测宿主类型：

| 边界 | 职责 |
| --- | --- |
| 宿主 JSON 归一化 | 转为 `AgentTurnEvent` |
| `AgentHookAdapter` | 只处理通用 BeforeRun/AfterRun |
| `AgentHookOutcome` | 渲染为宿主接受的输出 |

## 2. 普通使用者只配置两个运行值

```bash
# 运行值（URL + Token）
export MEMORY_MCP_URL=https://memory.example.com/mcp
export MEMORY_MCP_TOKEN=<该 Agent Host 自己的 Bearer Token>
# 或从模板加载
cp agent/.env.example examples/agent.env && chmod 600 examples/agent.env
set -a; source examples/agent.env; set +a
```

从同一终端启动 Agent。IDE、桌面应用或 systemd 进程若不继承该终端环境，必须通过
自身 Secret/环境注入机制提供同名变量。

Token 必须是 Server `MEMORY_MCP_AUTH_TOKENS` 中的一枚 key。owner 完全由服务端 Token
映射的 `tenant_id + subject_id` 派生，Agent 侧不配置 owner、client_id 或 agent_id。

| 场景 | 要求 |
| --- | --- |
| 同一用户的不同 Agent | 可用不同 Token，映射到同一 tenant/subject |
| 不同用户 | 必须映射到不同 subject |

### 内部默认值（不属于普通用户配置）

| 项目 | 默认值 |
| --- | --- |
| `profile_id` | Agent 不发送；使用当前 Token 的服务端 `default_profile_id` |
| MCP 超时 | 15 秒 |
| fail-open | 开启 |
| recall | 最多 5 条、600 token |
| capture | 最多 3 次有界尝试 |
| 本地状态 | `<事件 cwd>/.memory-mcp/hooks/` |
| 状态 TTL | 24 小时 |

Server Principal 的 `default_profile_id` 缺省为 `general-work`。投研产品应把该 Agent
Token 在服务端映射为 `investment-research`，终端用户仍只接触 URL 和 Token。高级集成
可临时 `export MEMORY_HOOK_PROFILE_ID=investment-research` 显式覆盖。

投研关系能力不增加 Agent 环境变量。AfterRun 仍只提交一次完成轮次，关系识别在 Server
Capture 流程内自动发生：

| 阶段 | 行为 |
| --- | --- |
| 候选准入 | 先完成候选准入 |
| 端点选择 | 从本轮 auto-save 和同 owner/Profile 有效记忆中选择有界端点 |
| 关系保存 | 只保存显式、高置信且符合 Profile 方向的关系 |

关系原文必须命中用户消息；Assistant/Tool 自行得出的关系、pending、blocked 和歧义
关系不会自动建边。`link_memories` 继续作为治理工具。自动边绑定当时两端 revision，
后续 replacement 在 Server 事务内把旧边转为 stale，不需要 Agent 补发关系生命周期事件。

### 配置职责不要混合

| 配置 | 所在进程 | 作用 |
| --- | --- | --- |
| Server `.env` | Memory MCP Server | 数据库、模型、Token 到身份的映射 |
| Agent MCP 连接 | Agent Host | 让模型可以发现和手工调用 MCP 工具 |
| Agent Hook 注册 | Agent Host | 保证每个顶层轮次确定性调用主动记忆 |

Agent Host 的 URL/Token 应同时供 MCP 连接和 Hook 进程使用，但不要把服务端 `.env`
复制到 Agent，也不要用 Hook JSON 覆盖原有 MCP 配置。已有 `hooks` 时合并两个事件项；
已有同名 Memory Hook 只保留一份。

"只配置地址和 Token"指运行值只有两个；MCP 协议不能远程改写宿主本地配置，所以每种
Host 仍需一次性安装轻量 Agent 包并完成静态 Hook 注册。

## 3. 安装 Hook 命令

```bash
# 正式 Agent Host：从 wheel 安装（推荐版本化制品）
uv tool install /path/to/memory_mcp_agent-0.2.0-py3-none-any.whl
command -v memory-mcp-hook

# 仓库内构建 wheel
uv build --package memory-mcp-agent --wheel

# 从源码 checkout 安装（只指向 Agent 子包）
uv tool install ./agent

# 仓库开发环境
uv sync --all-packages --frozen && test -x .venv/bin/memory-mcp-hook

# 确认 stdout 只有 JSON（预期输出 {}）
printf '%s' '{"session_id":"check","turn_id":"check","cwd":"/tmp","hook_event_name":"SubagentStop"}' | memory-mcp-hook
```

包已发布到组织 registry 时，把 wheel 路径换成 `memory-mcp-agent==0.2.0`，不要在生产
命令中省略版本。以上方式都不会安装根发行包 `memory-mcp`。

| 安装产物 | 说明 |
| --- | --- |
| `memory-mcp-hook` 命令 | 供宿主调用的入口 |
| `memory_mcp_agent` Python API | 供框架嵌入使用 |
| 轻量依赖 | HTTP、配置、数据校验 |
| 不包含 | PostgreSQL driver、LangChain、模型 Provider、ASGI Server、数据库配置 |

Agent 包要求 Python 3.11+（Server 的 Python 3.14 不传递）。共享 `.venv` 只是开发
便利，配置推荐使用 `command -v memory-mcp-hook` 返回的绝对路径。

## 4. 通用 Agent 合同

### 4.1 直接使用 command Hook

宿主支持"事件 JSON 写入 stdin，命令 JSON 从 stdout 返回"时，直接映射到以下标准输入：

> **编码约定**：宿主按 UTF-8 把事件 JSON 写入 stdin，hook 也按 UTF-8 把结果
> 写到 stdout。Windows 中文系统默认 stdin/stdout 编码是 GBK/CP936，直接用
> 文本 stdin 会让 UTF-8 字节被按 GBK 解码（轻则中文乱码，重则
> `stdin_decode_error`）。`memory-mcp-hook` 入口已强制用 UTF-8 读写二进制
> 缓冲绕开系统默认编码，宿主只需保证发 UTF-8 即可。

| 事件 | 标准输入 JSON |
| --- | --- |
| BeforeRun | `{"hook_event_name":"BeforeRun","conversation_id":"...","run_id":"...","cwd":"/abs/path","user_input":"本轮用户原始输入"}` |
| AfterRun | `{"hook_event_name":"AfterRun","conversation_id":"...","run_id":"...","cwd":"/abs/path","final_output":"本轮最终助手回复"}` |

| 字段 | 要求 |
| --- | --- |
| `conversation_id` | 同一会话稳定，非空 |
| `run_id` | 每个顶层轮次唯一，Before/After 完全相同 |
| `cwd` | 可信的绝对工作目录，Before/After 完全相同 |
| `user_input` | BeforeRun 必需，当前顶层用户输入 |
| `final_output` | AfterRun 必需，已经成功形成的最终回复 |

命令也直接兼容首批宿主字段：

| 通用语义 | Codex | Claude Code |
| --- | --- | --- |
| Before/After | `UserPromptSubmit` / `Stop` | 同左 |
| 会话 | `session_id` | `session_id` |
| 轮次 | `turn_id` | `prompt_id` |
| 用户输入 | `prompt` | `prompt` |
| 最终回复 | `last_assistant_message` | 同左 |

多个别名可同时出现，但值必须一致；不同值会以稳定错误码 fail-open。未知事件和
`SubagentStop` 返回 `{}` 且无副作用。

| 返回场景 | 输出 |
| --- | --- |
| 有召回内容 | `hookSpecificOutput.additionalContext` |
| 无召回或捕获成功 | `{}` |
| 可见但不阻止主流程的诊断 | `systemMessage` |

输出协议不同的宿主只需写一个薄 renderer，把公开的
`AgentHookOutcome(additional_context, warning_code)` 映射为宿主格式。

### 4.2 直接嵌入 Agent Framework

框架能在单进程内包住顶层调用时，优先使用 `HookedAgentRunner`，不需要 command Hook
或跨进程状态文件：

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
    profile_id=settings.profile_id, conversation_id=conversation_id, turn_id=turn_id
)  # 通用 general-work；投研 investment-research
async with MemoryMcpClient(settings) as client:
    bridge = MemoryHookBridge(client, settings)
    result = await HookedAgentRunner(bridge, call_agent).run(context, user_input)
```

`call_agent(user_input, memory_context)` 应把记忆当作不可信历史上下文，并返回顶层最终
回复。可运行示例见 [`examples/hook_runner.py`](../examples/hook_runner.py)。

### 4.3 新宿主兼容性检查

接入新 Agent 前确认以下条件。缺少稳定轮次标识时不要用 prompt hash、时间戳或
"最新状态"猜测关联（并发轮次会串数据），应先增加可靠标识或使用单进程 Runner。

| # | 检查项 |
| --- | --- |
| 1 | 模型请求前有顶层 Before 事件 |
| 2 | 最终回复形成后有顶层 After 事件 |
| 3 | 两个事件共享稳定会话和轮次标识 |
| 4 | Before 能同步注入附加上下文 |
| 5 | After 能取得最终助手回复 |
| 6 | 工具和子 Agent 事件能与顶层事件区分 |
| 7 | command Hook 超时至少覆盖 20 秒 recall、60 秒 capture |

## 5. 宿主配置

Codex 原生支持 `UserPromptSubmit` 和 `Stop` command Hook（官方参考
[Codex Hooks](https://learn.chatgpt.com/docs/hooks)）。Claude Code 适配器依赖稳定的
`prompt_id`，版本必须不低于 `2.1.196`（`claude --version`）。两个宿主均无原生 HTTP
Hook，跨机接入仍需本地 `memory-mcp-agent` 命令向远端 MCP URL 发起 HTTP 请求。

| 宿主 | 位置 | 范围 |
| --- | --- | --- |
| Codex | `~/.codex/hooks.json` | 当前用户的所有项目 |
| Codex | `<project>/.codex/hooks.json` | 当前可信项目 |
| Claude Code | `~/.claude/settings.json` | 当前用户的所有项目 |
| Claude Code | `<project>/.claude/settings.json` | 项目共享配置 |
| Claude Code | `<project>/.claude/settings.local.json` | 当前用户的项目本地配置 |

### Codex

复制 [Codex Hook 示例](../examples/agents/codex-hooks.json) 或合并其中的 `hooks`
对象。若命令不在 `PATH`，把两个 `command` 替换为绝对路径。Codex 会审核非托管
command Hook：启动后 `/hooks` → 检查来源和命令 → 信任两个 Hook → `/mcp` 确认
Memory MCP 为 connected。不要在同一配置层同时维护 `hooks.json` 和 `config.toml`
内联 `[hooks]` 的同一 Hook。

### Claude Code

首次测试推荐 `.claude/settings.local.json`。复制
[Claude Code 示例](../examples/agents/claude-code-settings.json) 或只合并其中的
`hooks` 对象。进入后：`/status` 确认 settings 已加载 → `/hooks` 确认两个顶层 Hook
指向 `memory-mcp-hook` → 确认 Memory MCP 连接正常。官方文档见
[Claude Code Hooks](https://code.claude.com/docs/en/hooks)。

## 6. 验证、本地状态与故障排查

### 6.1 手工端到端验证

完整端到端验证见[端到端使用](usage.md#4-真实模型闭环)。Agent Hook 特有要点：

| # | 验证步骤 | 预期 |
| --- | --- | --- |
| 1 | 启动前确认 `test -n "$MEMORY_MCP_URL" && test -n "$MEMORY_MCP_TOKEN"` | 两变量非空 |
| 2 | 第一轮产生长期记忆后查看 `agent-hook.log` | `recall.completed` 与 `capture.completed` |
| 3 | 第二轮用新轮次询问同一主题 | `recalled_count > 0`，无需手工调用 |
| 4 | 同 tenant/subject 另一 Token 启动另一宿主 | 仍能召回；换不同 subject 则 `recalled_count=0` |
| 5 | 验证投研 Profile | 设置 `default_profile_id` 或 `export MEMORY_HOOK_PROFILE_ID=investment-research` |

日志路径：`tail -n 50 .memory-mcp/logs/agent-hook.log`。

### 6.2 本地状态

command Hook 的 Before 和 After 是两个独立进程。原始用户输入短期保存在
`<事件 cwd>/.memory-mcp/hooks/<identifier-digest>.json`：

| 属性 | 说明 |
| --- | --- |
| 目录/文件权限 | 目录 `0700`，文件 `0600` |
| 文件名 | 只含会话/轮次标识的 SHA-256 摘要 |
| Before | 先保存 prompt |
| After | 网络调用前原子补齐 final output、固定 observed time 和可选 Profile |
| 完成后 | `completed` 或明确 `failed` 后删除；warning 或 `reprocess_required` 保留 |
| 补送 | 后续任意一次 Stop 在当前轮次前最多补送一条旧 payload |
| schema | 新状态使用 v2；v1 只含 prompt，可在 Stop 原子升级；全部按 24h TTL 清理 |
| 约束 | 不解析宿主 transcript |

Hook 日志位于 `<Hook 进程 cwd>/.memory-mcp/logs/agent-hook.log`，只记录阶段、稳定
引用、数量、状态和错误码，不记录 prompt、回复或 Token。服务端日志由
`MEMORY_MCP_LOG_FILE` 控制，详见[日志规范](logging.md)。

### 6.3 故障排查

| 表现/错误码 | 原因与处理 |
| --- | --- |
| `/hooks` 看不到配置 | 检查文件位置、JSON、项目信任和宿主版本 |
| `command not found` | 使用绝对命令路径，确认 Agent 进程 `PATH` |
| `configuration_error` | Hook 进程缺少 URL 或 Token |
| `missing_conversation_identifier` | 事件缺少稳定会话标识 |
| `missing_turn_identifier` | 宿主版本过旧或事件缺少稳定轮次标识 |
| `missing_turn_state` | Hook 中途启用、Before 未保存，或 Before/After 的 cwd/ID 不同 |
| `recall_memory_mcp_unavailable` | 检查地址、网络、TLS、服务和超时；本轮 fail-open |
| `capture_memory_mcp_unavailable` | 捕获未成功；完整 payload 已留在本机，后续 Stop 会补送 |
| `capture_reprocess_required` / failure code | retryable 保留补送；明确 failed 删除并提示检查模型/准入 |
| recall 为 0 | 记忆未 auto-save、相关性不足、owner 不同或已非 active/current |

```bash
curl --fail "${MEMORY_MCP_URL%/mcp}/health"  # 非 /mcp 结尾时用部署文档的实际 health 地址
```

## 7. 当前限制与回滚

| 限制 | 说明 |
| --- | --- |
| 配置不可远程改写 | MCP Server 不能自动修改 Agent 本地 Hook 配置；Codex 无原生 HTTP Hook，跨机接入由本地轻量 command Client 转发 |
| Hook 组合约束 | 不要与会在 Stop 阶段要求同一轮继续执行的其他 Hook 组合 |
| 本地 outbox 边界 | best-effort 跨 Hook 进程和短时网络故障补送；要求永久下线/磁盘损坏/无后续 Stop 仍保证投递时，应另行设计集中 durable outbox/queue worker |
| renderer 覆盖范围 | command renderer 当前直接覆盖 Codex/Claude Code 公共 JSON；其他输出协议需要薄映射，核心生命周期和 MCP 合同不变 |

回滚只需从宿主配置移除或禁用两个 Hook。Memory MCP 工具、已有记忆和服务端数据不会被
删除，手工工具调用仍可继续使用。
