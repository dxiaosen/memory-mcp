# Memory MCP Agent

`memory-mcp-agent` 是远程 Memory MCP Server 的轻量主动记忆客户端。

安装后只提供 `memory-mcp-hook` 和 `memory_mcp_agent` Python API，不包含服务端、
PostgreSQL、LangChain、模型 Provider、ASGI Server 或数据库迁移命令。要求
Python 3.11+，运行时只配置：

```dotenv
MEMORY_MCP_URL=https://memory.example.com/mcp
MEMORY_MCP_TOKEN=<该 Agent Host 的 Bearer Token>
```

Agent 默认不发送 Profile；Server 根据该 Token 的 `default_profile_id` 选择
`general-work` 或 `investment-research`。高级进程级覆盖可使用
`MEMORY_HOOK_PROFILE_ID`，普通接入不需要。

command Hook 使用事件工作目录中的受限原子状态形成 24 小时 best-effort outbox；
短时网络失败或服务要求重处理时由后续 Stop 有界补送，不需要本地 Server 或消息队列。

安装 wheel：

```bash
uv tool install /path/to/memory_mcp_agent-0.2.0-py3-none-any.whl
command -v memory-mcp-hook
```

Python Framework 直接集成时安装同一个发行包，然后从 `memory_mcp_agent` 导入
`MemoryMcpClient`、`MemoryHookBridge`（BeforeRun 召回注入）。

完整配置和 Codex、Claude Code、通用宿主接入步骤见源码仓库
`docs/agents.md`。
