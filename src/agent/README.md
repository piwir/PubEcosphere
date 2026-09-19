# src/agent — 流水线的 agent 封装（MCP server + subagent 提示词）

同一套 CLI 与 `docs/prompts/` 写稿提示词，两种挂载形态，按场景选用：

| 形态 | 载体 | 适用场景 |
| --- | --- | --- |
| MCP server | `mcp_server.py`（`mcp.sh` 启动） | 定时 / 无头客户端：17 个确定性工具转发 |
| subagent 提示词 | `pubecosphere-weekly.md` / `pubai4s-post.md` | 交互式 agent：全流程编排 + 写稿一体 |

**分工原则**：确定性步骤（抓取 / 打分 / 图材 / 排版渲染）走 CLI 或 MCP 工具；LLM 写稿默认由 subagent 本体按 `docs/prompts/` 提示词完成（不依赖外部接口），「走 API」时用 `generate` / `pubai4s_run`。两形态不复制编排知识：流程细节只在 `run_issue.sh` 头注释与两份提示词里。

**前提**：`pip install -e .`，推文流水线另需 `pip install -e submodules/PubAI4S`（否则 `pubai4s_*` 三工具不可用）；vendored md2html 就绪（`cd src/vendor/md2html-cli && npx -y bun install`）。

## MCP server（17 工具）

**挂载**（客户端的 MCP 配置里添加，必须**绝对路径**启动脚本）：

```json
{
  "mcpServers": {
    "pubecosphere": {
      "command": "bash",
      "args": ["/abs/path/to/PubEcosphere/src/agent/mcp.sh"]
    }
  }
}
```

> 客户端健康检查时 cwd=`/`、PATH 无 conda，`mcp.sh` 负责自切仓库根 + 解析 python；找不到 python 时加 `"env": {"PYTHON": "/path/to/python"}`。

- 周报 14 工具：`status` / `preflight` / `capture` / `revisit` / `rank` / `pick` / `material` / `generate` / `assemble` / `md2html` / `polish_html` / `inline_images` / `run_issue` / `site_sync`。
  - `revisit` 支持 `window`（相对天）与 `window_dates`（绝对 ISO 日期，起含止不含）二选一；`rank` 默认按正整数期号自动推绝对窗口，可传 `window_dates` / `window_field` / `force_deep`。
  - `site_sync` 把本期期号/日期/素材窗口/归档条目写进 `src/site/src/data/site.json`（公众号链接除外，发布后在 `issues[].wechatUrl` 补链接再跑一次），幂等、不 commit、不 push。
- 推文 3 工具：`pubai4s_fetch`（抓取 inputs，不调 LLM）/ `pubai4s_render`（post.md → base64 html）/ `pubai4s_run`（全流程走 .env 模型，供无头场景）。

**使用建议**：agent 起步先 `status` 自查；`pick` 先 `dry_run=true` 给人工批准；写稿产物人工审核后再发布。

## subagent 提示词

把对应文件内容作为 subagent 的系统提示词加载，并授予 Bash / Read / Write / Edit / Glob / Grep 权限；工作目录指向本仓库根：

- `pubecosphere-weekly.md` — 周报全流程：status 查询 → rank 打分 → pick 选稿（先 dry-run 等人工确认）→ 图材 → 写稿 → 排版渲染；
- `pubai4s-post.md` — AI4S 项目推文：fetch 抓取 → 材料提取 → 写稿 → 渲染 base64 成品。

宿主若已挂载本 MCP server，subagent 可用同名工具替代直接 Bash 调 CLI；未挂载时直接跑 `python -m` 命令，两者等价。
