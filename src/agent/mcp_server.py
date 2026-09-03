"""极简 stdio MCP server（JSON-RPC 2.0 over stdin/stdout，纯 stdlib）。

实现协议子集：initialize（协商 + capabilities.tools）、ping、tools/list、
tools/call；`notifications/initialized` 等无 id 通知忽略不回。未知方法返回
JSON-RPC 错误码 -32601。

stdout 只走协议（每行一个 JSON 消息）；日志一律 stderr。

用法：
    python -m agent.mcp_server                  # stdio 服务（挂载命令）
    python -m agent.mcp_server --list-tools
    python -m agent.mcp_server --selftest       # 进程内 JSON-RPC 往返自测
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import TextIO

from . import __version__
from .tools import TOOLS, run_tool

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "pubecosphere"


class McpServer:
    def __init__(self, stdin: TextIO | None = None, stdout: TextIO | None = None):
        self.stdin = stdin if stdin is not None else sys.stdin
        self.stdout = stdout if stdout is not None else sys.stdout

    def _send(self, msg: dict) -> None:
        self.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.stdout.flush()

    def handle(self, msg: dict) -> None:
        msg_id = msg.get("id")
        if msg_id is None:
            return  # notification（如 notifications/initialized），不回
        method = msg.get("method")
        params = msg.get("params", {}) or {}

        if method == "initialize":
            self._send({
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": __version__},
                },
            })
        elif method == "ping":
            self._send({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        elif method == "tools/list":
            self._send({
                "jsonrpc": "2.0", "id": msg_id,
                "result": {"tools": [
                    {"name": t.name, "description": t.description,
                     "inputSchema": t.input_schema}
                    for t in TOOLS.values()]},
            })
        elif method == "tools/call":
            name = params.get("name", "")
            arguments = params.get("arguments", {}) or {}
            try:
                result = run_tool(name, arguments)
                self._send({
                    "jsonrpc": "2.0", "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": result.text}],
                        "isError": not result.ok,
                    },
                })
            except Exception as exc:  # noqa: BLE001 —— 参数/工具异常也回 JSON-RPC 错误
                self._send({
                    "jsonrpc": "2.0", "id": msg_id,
                    "result": {
                        "content": [{"type": "text",
                                     "text": f"工具调用失败：{type(exc).__name__}: {exc}"}],
                        "isError": True,
                    },
                })
        else:
            self._send({
                "jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32601, "message": f"未知方法：{method}"},
            })

    def serve(self) -> int:
        """逐行读 stdin、逐条应答 stdout，EOF 即退出。"""
        for line in self.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                self._send({
                    "jsonrpc": "2.0",
                    "error": {"code": -32700, "message": f"解析错误：{exc}"},
                })
                continue
            self.handle(msg)
        return 0


# ---- CLI 派发（--selftest / --list-tools / 默认 serve） -------------------

def _list_tools() -> int:
    print(f"{len(TOOLS)} 个工具：")
    for name in sorted(TOOLS):
        t = TOOLS[name]
        props = ", ".join(t.input_schema.get("properties", {}).keys()) or "（无参数）"
        req = ", ".join(t.input_schema.get("required", []))
        print(f"  {name:<14} 参数: {props}" + (f"  必填: {req}" if req else ""))
    return 0


class _Sink:
    """内存 stdout 替身：逐行记录，供进程内自测断言。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, s: str) -> None:
        self.lines.append(s)

    def flush(self) -> None:
        pass


def _selftest() -> int:
    """进程内 JSON-RPC 往返：initialize → tools/list → status → md2html --check → ping。

    只做结构性断言（协议/工具表/返回形状），不依赖真实 DB/网络：
    status 对缺失 DB 也会退出 0（报告 db_exists=false），md2html --check 离线确定性。
    """
    sink = _Sink()
    server = McpServer(stdout=sink)
    failures: list[str] = []

    def call(req: dict) -> dict:
        sink.lines.clear()
        server.handle(req)
        if not sink.lines:
            raise AssertionError(f"无响应：{req.get('method')}")
        return json.loads(sink.lines[0])

    # 1) initialize
    r = call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                         "clientInfo": {"name": "selftest", "version": "0"}}})
    if r.get("result", {}).get("protocolVersion") != PROTOCOL_VERSION:
        failures.append("initialize 协议版本不一致")
    if r.get("result", {}).get("capabilities", {}).get("tools") is None:
        failures.append("initialize capabilities.tools 缺失")

    # 2) notifications/initialized —— 无 id，不应有响应
    sink.lines.clear()
    server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    if sink.lines:
        failures.append("通知不应有响应")

    # 3) tools/list
    r = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = r.get("result", {}).get("tools", [])
    if len(tools) != len(TOOLS):
        failures.append(f"tools/list 数量 {len(tools)} != {len(TOOLS)}")
    names = {t["name"] for t in tools}
    if "status" not in names or "run_issue" not in names:
        failures.append("tools/list 缺少核心工具")

    # 4) tools/call status（只读；DB 缺失也退出 0）
    r = call({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "status", "arguments": {"json": True}}})
    if r.get("error"):
        failures.append(f"status 调用返回协议错误：{r['error']}")
    else:
        content = r.get("result", {}).get("content", [])
        text = content[0].get("text", "") if content else ""
        if "db_exists" not in text:
            failures.append("status 返回里没有 db_exists 字段")

    # 5) tools/call md2html --check（离线确定性）
    r = call({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
              "params": {"name": "md2html", "arguments": {"check": True}}})
    if r.get("error"):
        failures.append(f"md2html --check 调用协议错误：{r['error']}")

    # 6) ping
    r = call({"jsonrpc": "2.0", "id": 5, "method": "ping"})
    if r.get("result") != {}:
        failures.append("ping 应答不是 {}")

    # 7) 未知方法 → -32601
    r = call({"jsonrpc": "2.0", "id": 6, "method": "no_such_method"})
    if r.get("error", {}).get("code") != -32601:
        failures.append("未知方法应返回 -32601")

    # 8) 未知工具 → isError
    r = call({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
              "params": {"name": "nope", "arguments": {}}})
    if not r.get("result", {}).get("isError"):
        failures.append("未知工具应返回 isError=true")

    if failures:
        print("selftest 失败：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"selftest: {len(TOOLS)} 个工具已注册，JSON-RPC 往返 8 项全绿（initialize/tools/list/status/md2html/ping/错误处理）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agent.mcp_server", description=__doc__)
    ap.add_argument("--selftest", action="store_true", help="进程内 JSON-RPC 往返自测")
    ap.add_argument("--list-tools", action="store_true", help="打印工具清单")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if args.list_tools:
        return _list_tools()
    return McpServer().serve()


if __name__ == "__main__":
    raise SystemExit(main())
