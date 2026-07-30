"""支持通过 ``python -m memory_mcp`` 启动 Memory MCP 服务。"""

from .server.app import main

if __name__ == "__main__":
    main()
