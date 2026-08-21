#!/usr/bin/env bash
# MCP server 启动入口（环境无关）：MCP 客户端健康检查/启动 server 时 cwd 常为 “/”、PATH 无 conda，
# 这里先切到仓库根、再解析一个可用的 python，保证 `python -m agent.mcp_server` 能跑起来。
#
# 挂载：任何 MCP 客户端在 MCP 配置里添加（绝对路径——健康检查 cwd 是 /，相对路径会失效）：
#   { "mcpServers": { "pubecosphere": { "command": "bash", "args": ["/abs/path/to/PubEcosphere/agent/mcp.sh"] } } }
#
# 换机器/换 python：设 PYTHON 环境变量，或把 conda 加进 PATH（例如 conda activate）。
set -euo pipefail

# 1) 切到仓库根（本文件在仓库根/agent 下）
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 2) 解析 python：PYTHON 覆盖 → PATH 上的 python → python3 → 常见 conda 路径
resolve_python() {
    if [[ -n "${PYTHON:-}" && -x "$PYTHON" ]]; then printf '%s' "$PYTHON"; return; fi
    if command -v python >/dev/null 2>&1; then printf '%s' "$(command -v python)"; return; fi
    if command -v python3 >/dev/null 2>&1; then printf '%s' "$(command -v python3)"; return; fi
    for d in "$HOME/anaconda3/bin" "/opt/conda/bin"; do
        if [[ -x "$d/python" ]]; then printf '%s' "$d/python"; return; fi
    done
    return 1
}

PYTHON_BIN="$(resolve_python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
    echo "[mcp.sh] 找不到可用 python（设 PYTHON 环境变量或把 conda 加进 PATH）" >&2
    exit 1
fi
export PATH="$(dirname "$PYTHON_BIN"):$PATH"   # 让子命令（python -m …）也用它

exec "$PYTHON_BIN" -m agent.mcp_server "$@"
