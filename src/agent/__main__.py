"""`python -m agent` 入口：委托给 mcp_server.main（默认 stdio 服务，或 --selftest / --list-tools）。

挂载：在 MCP 客户端配置中添加 `bash <repo>/src/agent/mcp.sh`（见 src/agent/README.md）。
自测：python -m agent.mcp_server --selftest
"""
from __future__ import annotations

from .mcp_server import main

if __name__ == "__main__":
    raise SystemExit(main())
