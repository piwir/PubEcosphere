# PubEcosphere MCP server

把 PubPeer 周报流水线封装成 MCP server，挂载到任何 agent 当作「个人 agent 的一个小组件」。

## 原理

Server 是**无状态薄层**：智能在确定性 CLI + 机器可读状态（`scoring status`）+ 明确退出码里。
`tools/call` 只是把每个工具翻译成一条 `python -m` 子进程命令（list-arg subprocess，`shell=False`），
跑完把 stdout/stderr/退出码/产物路径拼成文本返回。不联网、不写 DB（除各工具自身语义）。

**人工门槛语义**：`pick` 分数线、草稿审核、微信推送**永远由人拍板**。工具 `description`
写明顺序（先 dry-run 预览 → 人工确认 → 正式执行），调用方 agent 按此行为驱动。

## 安装 / 挂载

```bash
pip install -r requirements.txt                      # 仓库根（Pillow）
cd vendor/md2html-cli && npx -y bun install          # md→html 转换器依赖（首次联网）
cp .env.example .env                                 # 填 PUBECOSPHERE_LLM_API_KEY（仅 generate 需要）

# 任何 MCP 客户端（pi agent 等）：在 MCP 配置里添加（必须绝对路径启动脚本，见下）
{
  "mcpServers": {
    "pubecosphere": {
      "command": "bash",
      "args": ["/abs/path/to/PubEcosphere/agent/mcp.sh"]
    }
  }
}
```

server 自推导仓库根（`agent/runner.py`），无需指定 cwd。

> **启动环境坑**：MCP 客户端健康检查/启动 server 时 cwd 常是 `/`、PATH 不含 conda，直接 `python -m agent.mcp_server` 会 `ModuleNotFoundError` / `python: not found`。`agent/mcp.sh` 负责两件事：切到仓库根 + 解析可用 python（依次找 `$PYTHON` 环境变量 → `python` → `python3` → 常见 conda 路径）。换机器找不到 python 时，在配置里加 `"env": {"PYTHON": "/path/to/python"}`。

## 自检

```bash
python -m agent.mcp_server --selftest    # 进程内 JSON-RPC 往返 8 项
python -m agent.mcp_server --list-tools
```

## 工具清单（14 个）

| 工具 | 参数（必填加粗） | 行为 / 产物 | 门槛指引 |
| --- | --- | --- | --- |
| `status` | issue?, json? | 只读状态：DB 行数 / 最新 run_id / 各期阶段 / `next_step` | **起步先跑它** |
| `preflight` | — | 环境自检：提示词校验 + 离线自测 + md2html 就绪 + status 可读 | 挂载后确认可用 |
| `capture` | max_offset? | 每日捕获 feed（/api/recent 约 400 条） | 独立每日操作；生产由**长期 cron 供给**，`run_issue.sh` 默认不跑 |
| `revisit` | window?, window_dates?, limit? | 回访完整评论线程（幂等 upsert，可断点续跑） | 同上，默认由 cron 供给 |
| `rank` | **issue**, window?, window_dates?, window_field?, run_id? | 两阶段打分 → `output/issue/<n>/score/<run_id>/`；正整数期号不传 window 时自动按期号推绝对窗口 | 停等人工审短名单 |
| `pick` | **issue**, min_score?, max_total?, dry_run? | 每类选稿（总数上限 max_total，默认 25）+ 当期下载评论图 + manifest | **先 dry_run=true 预览，确认分数线后再正式选** |
| `material` | **issue**, max_images? | 三图合并 → `output/issue/<n>/material/` | 清场重建 |
| `flatten` | **issue** | 摊平 upload 文件夹 → `output/issue/<n>/upload/` | 流水线步骤，亦可供人工手动上传 |
| `generate` | **issue**, assemble?, model?, dry_run?, week_start? | 单模型提取+写稿 → `weekly/stageA_combined.md` + `_draft.md`；自动注入 WEEK_START | **草稿需人工审核**；需 API key |
| `assemble` | **issue**, dry_run? | 排版成品 md + 图复制 | 审完草稿后调 |
| `md2html` | **md_path**(限 output/), keep_title?, theme?, check? | md → 微信兼容 HTML（vendored 转换器） | — |
| `polish_html` | **html_path**(限 output/) | 上下标 + 页脚小字 + GitHub 链接修复（幂等） | — |
| `inline_images` | **html_path**(限 output/) | base64 内联 → `*-base64.html`（微信粘贴用） | 推送前最后一步 |
| `run_issue` | **issue**, min_score?, dry_run? | 一键 `run_issue.sh` 全流程 | dry_run=true 只预览 pick |

## 状态机（status 推导 next_step）

`captured → ranked（score/ 有 run 目录）→ picked（manifest.json）→ materialized（material/index.md）→ flattened（upload/ 非空）→ drafted+assembled（weekly/stageA_combined.md + <n>.md）→ html（<n>.html）→ base64（<n>-base64.html）→ ready_to_publish`

某期卡住时 `status --issue <n>` 会直接告诉你下一步该调哪个工具。

## 参数约定

- `issue`：正整数期号或 `-1`（测试期），其余拒绝。
- 打分窗口：`window`（相对 'D1 D2'）与 `window_dates`（绝对 'START END' ISO 日期，起含止不含）互斥；正整数期号都不传时自动按 `WEEK_START_BASE+(期号-1)*7` 推绝对窗口（与 run_issue.sh / 导语标签同算法）；`-1` 不传则不过滤。
- md/html 路径：必须 `output/` 内相对路径（如 `output/issue/1/weekly/1.md`），防越界读写。
- 所有工具返回值：`exit_code` + `stdout` + `stderr` +（`preflight`/`status` 含机器可读段）。

## 架构

```
agent/
├── mcp_server.py    # stdio MCP 协议（JSON-RPC 2.0 子集：initialize/ping/tools/list/tools/call）
├── runner.py        # run_cli：list-arg subprocess，repo_root 自推导
├── tools.py         # 14 工具：参数校验 → 命令构造 → 执行
└── __main__.py      # python -m agent 委托
```

纯 stdlib，无额外 Python 依赖（requirements.txt 只有 Pillow，供流水线 image 合并用，非 server 必需）。
