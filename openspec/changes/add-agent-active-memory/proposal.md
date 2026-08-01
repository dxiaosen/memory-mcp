## Why

Memory MCP 已经提供召回与完成轮次捕获能力，但当前只有框架无关的参考 Runner，
Agent Host 不能仅靠已配置的 MCP 连接自动在每个顶层用户轮次执行
BeforeRun/AfterRun。需要补齐通用生命周期适配，并先提供 Codex、Claude Code
配置模板，使用户只提供 Memory MCP 地址和 Bearer Token，就能获得确定性的主动
召回与自动捕获。

## What Changes

- 新增统一的 Agent Hook 命令和宿主无关的顶层轮次事件/结果合同，从标准输入读取
  command Hook 事件；内置兼容 Codex 与 Claude Code 的
  `UserPromptSubmit`、`Stop` 字段，并允许其他 Agent 映射到标准
  `BeforeRun`、`AfterRun`。
- 在模型处理当前提示前调用 `recall_memory`，将服务端渲染的历史记忆作为
  `additionalContext` 注入；召回失败默认 fail-open。
- 在顶层轮次成功停止后，使用稳定会话/轮次标识、原始用户输入和最终助手输出调用
  `capture_completed_turn`，并保持服务端幂等。
- 增加权限受限、原子写入、自动清理的本地轮次状态，用于关联两个独立 Hook
  进程；状态不进入模型上下文或运行日志。
- 将 Agent Host 的必需连接配置收敛为且只接受 `MEMORY_MCP_URL` 和
  `MEMORY_MCP_TOKEN`；`profile_id`、
  超时、预算和重试均使用代码默认值。
- 将 Hook Client 从服务端发行包拆为独立的 `memory-mcp-agent` 轻量发行包；
  Agent Host 不安装 PostgreSQL、LangChain、模型 Provider 或服务端入口依赖。
- 让通用 MCP 召回/捕获工具在调用方省略 `profile_id` 时采用固定
  `general-work`，同时保留显式 profile 扩展能力。
- 提供通用 Agent 接入合同，以及 Codex 与 Claude Code 的可复制 Hook 配置、
  安装说明、验证步骤和故障排查。
- 不引入外部队列，不捕获子 Agent 的独立内部轮次，不改变认证 owner 推导、
  PostgreSQL 权威存储或模型抽取规则。

## Capabilities

### New Capabilities

- `agent-active-memory`: 定义通用 Agent 顶层轮次的确定性主动召回、自动捕获、
  最小配置、状态关联、失败语义和宿主适配要求；Codex 与 Claude Code 是首批
  内置 command Hook 配置。

### Modified Capabilities

无。

## Impact

- 受影响代码：独立 `memory_mcp_agent` 包、MCP 工具默认参数、Python CLI 入口。
- 受影响打包：仓库改为包含 `server` 与 `agent` 两个对称 member 的 virtual uv workspace；
  `memory-mcp-hook` 只由轻量 Agent 包提供。
- 受影响配置：Agent Host 连接变量统一为两个 `MEMORY_MCP_*` 变量，旧连接变量
  不再接受。
- 新增本地运行状态：Agent 工作目录下 `.memory-mcp/hooks/`，仅用于短期轮次关联。
- 新增宿主配置与文档：Codex `hooks.json`/`config.toml` 和 Claude Code
  `settings.json`/`settings.local.json`。
- 不为 Server 新增第三方生产依赖、数据库 migration、外部队列或身份参数；
  Agent 发行包只声明 HTTP/Pydantic 轻量运行依赖。
