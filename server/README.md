# Memory MCP Server

`memory-mcp` 是通过 Streamable HTTP 对外提供长期记忆能力的服务端发行包。

它包含 MCP transport、认证边界、记忆 Core、候选抽取、PostgreSQL Repository
和数据库迁移命令。Agent Host 不需要安装本包，只需安装轻量
`memory-mcp-agent` 或直接调用标准 MCP 工具。

生产配置模板位于本目录的 `.env.example`。完整架构、配置、部署和使用方式见
仓库根目录的 `docs/`。
