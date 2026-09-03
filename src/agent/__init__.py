"""PubEcosphere MCP server：把流水线 `python -m` 阶段命令包成 MCP 工具。

纯 stdlib 手写（仿 llm/client.py 克制风格），无状态薄层：
智能在确定性 CLI + 机器可读状态（scoring status）+ 明确退出码里，
server 只做 stdin/stdout 的 JSON-RPC 协议转发。

启动：python -m agent.mcp_server          # stdio 服务
      python -m agent.mcp_server --list-tools
      python -m agent.mcp_server --selftest   # 进程内 JSON-RPC 往返自测
"""

__version__ = "0.1.0"
