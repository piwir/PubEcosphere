"""MCP 工具定义 → 命令构造（无状态薄层：每工具一把命令，跑完即弃）。

设计：
- 智能在确定性 CLI + `scoring status` 机器可读状态 + 明确退出码里；
- 人工门槛（pick 分数线、草稿审核、微信推送）由调用方 agent 行为实现，
  工具 description 已写明「先 dry-run 预览给人工批准再正式执行」；
- 参数校验：issue 限 `^-?\d+$`（正整数期号 / -1 测试期）；md/html 路径限
  `output/` 内（防越界读写）；其余按 CLI 白名单透传。

每个工具返回 RunResult（exit_code + stdout + stderr + 产物路径备注），
由 mcp_server 包装成 MCP `content:[{type:text}]` + `isError`。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from .runner import REPO_ROOT, RunResult, run_cli

PY = "python"
DB_DEFAULT = "data/pubpeer.db"
WEEK_START_BASE = "2026-08-03"  # 第 1 期数据收集起始日（与 run_issue.sh 默认一致）


# ---- 参数校验 ------------------------------------------------------------

def _issue(raw: object) -> str:
    s = str(raw)
    ok = s == "-1" or (s.isdigit() and int(s) > 0)
    if not ok:
        raise ValueError(f"issue 必须是正整数期号或 -1 测试期，收到：{s!r}")
    return s


def _output_path(raw: object) -> Path:
    """md/html 路径：必须是仓库 output/ 内（agent 不应读写输出目录之外）。"""
    p = Path(str(raw))
    if p.is_absolute():
        raise ValueError(f"请给 output/ 下的相对路径，不要绝对路径：{raw!r}")
    full = (REPO_ROOT / p).resolve()
    out_root = (REPO_ROOT / "output").resolve()
    if not full.is_relative_to(out_root):
        raise ValueError(f"路径必须在 output/ 内：{raw!r}")
    return full


# ---- 命令规格 ------------------------------------------------------------

@dataclass
class CmdSpec:
    argv: list[str]
    env: dict[str, str] | None = None


Tool = Callable[[dict], CmdSpec]


def _issue_dir(n: str) -> str:
    return f"output/issue/{n}"


def _window_dates_for_issue(n: str, base: str = WEEK_START_BASE) -> tuple[str, str]:
    """期号 → 绝对窗口 [START, START+7d)（ISO 日期，起含止不含）。

    与 run_issue.sh / llm.generate.weekly_date_range 同算法：START = base + (期号-1)*7。"""
    start = date.fromisoformat(base) + timedelta(days=(int(n) - 1) * 7)
    return start.isoformat(), (start + timedelta(days=7)).isoformat()


# ---- 各工具 --------------------------------------------------------------

def _status(args: dict) -> CmdSpec:
    argv = [PY, "-m", "scoring.pipeline", "status", "--db", DB_DEFAULT]
    if args.get("issue"):
        argv += ["--issue", _issue(args["issue"])]
    if args.get("json"):
        argv.append("--json")
    return CmdSpec(argv)


def _preflight(args: dict) -> CmdSpec:
    # 特殊：多命令聚合，见 run_tool 里对 preflight 的分支
    return CmdSpec([])


def _capture(args: dict) -> CmdSpec:
    argv = [PY, "-m", "crawler.crawl", "--db", DB_DEFAULT, "capture"]
    if args.get("max_offset"):
        argv += ["--max-offset", str(int(args["max_offset"]))]
    return CmdSpec(argv)


def _revisit(args: dict) -> CmdSpec:
    argv = [PY, "-m", "crawler.crawl", "--db", DB_DEFAULT, "revisit"]
    win = args.get("window")
    if win:
        parts = str(win).split()
        if len(parts) != 2 or not all(p.lstrip("-").isdigit() for p in parts):
            raise ValueError(f"window 须为 'D1 D2' 两个整数（如 '10 3'）：{win!r}")
        argv += ["--window", parts[0], parts[1]]
    if args.get("limit"):
        argv += ["--limit", str(int(args["limit"]))]
    return CmdSpec(argv)


def _rank(args: dict) -> CmdSpec:
    n = _issue(args["issue"])
    argv = [PY, "-m", "scoring.pipeline", "rank", "--db", DB_DEFAULT, "--issue", n]
    win = args.get("window")
    wdates = args.get("window_dates")
    if win and wdates:
        raise ValueError("window 与 window_dates 互斥，只能传一个")
    if win:
        parts = str(win).split()
        if len(parts) != 2 or not all(p.lstrip("-").isdigit() for p in parts):
            raise ValueError(f"window 须为 'D1 D2' 两个整数：{win!r}")
        argv += ["--window", parts[0], parts[1]]
    elif wdates:
        parts = str(wdates).split()
        if len(parts) != 2:
            raise ValueError(f"window_dates 须为 'START END' 两个 ISO 日期：{wdates!r}")
        argv += ["--window-dates", parts[0], parts[1]]
    elif n != "-1":
        # 与 run_issue.sh 一致：正整数期号按期号自动推算绝对窗口（-1 测试期保持不传）
        argv += ["--window-dates", *_window_dates_for_issue(n)]
    # 与 run_issue.sh 一致：默认 captured_at（CLI 本身默认 last_commented，故显式传）
    argv += ["--window-field", str(args.get("window_field") or "captured_at")]
    if args.get("run_id"):
        argv += ["--run-id", str(args["run_id"])]
    return CmdSpec(argv)


def _pick(args: dict) -> CmdSpec:
    n = _issue(args["issue"])
    argv = [PY, "-m", "scoring.pipeline", "pick", "--db", DB_DEFAULT, "--issue", n]
    # 与 run_issue.sh 一致：默认 0.50（config 的 0.45 只是 CLI 无 --min-score 时的回退）
    ms = args["min_score"] if args.get("min_score") is not None else 0.50
    argv += ["--min-score", str(float(ms))]
    if args.get("max_total") is not None:
        argv += ["--max-total", str(int(args["max_total"]))]
    if args.get("dry_run"):
        argv.append("--dry-run")
    return CmdSpec(argv)


def _material(args: dict) -> CmdSpec:
    n = _issue(args["issue"])
    argv = [PY, "-m", "scoring.material",
            "--pub-dir", f"{_issue_dir(n)}/pub",
            "--out", f"{_issue_dir(n)}/material"]
    if args.get("max_images"):
        argv += ["--max-images", str(int(args["max_images"]))]
    return CmdSpec(argv)


def _flatten(args: dict) -> CmdSpec:
    n = _issue(args["issue"])
    argv = [PY, "-m", "llm", "flatten",
            "--material-dir", f"{_issue_dir(n)}/material",
            "--upload-dir", f"{_issue_dir(n)}/upload",
            "--manifest", f"{_issue_dir(n)}/manifest.json"]
    return CmdSpec(argv)


def _generate(args: dict) -> CmdSpec:
    n = _issue(args["issue"])
    argv = [PY, "-m", "llm", "generate",
            "--material-dir", f"{_issue_dir(n)}/material",
            "--weekly-dir", f"{_issue_dir(n)}/weekly",
            "--issue", n]
    if args.get("assemble"):
        argv.append("--assemble")
    if args.get("model"):
        argv += ["--model", str(args["model"])]
    if args.get("dry_run"):
        argv.append("--dry-run")
    # WEEK_START 注入：只传基准起始日，期号偏移由 generate 内部 weekly_date_range
    # 按 (issue-1)*7 推算——与 run_issue.sh 同规则；勿在此再偏移（否则 --issue 2 起多 7 天）
    ws = str(args["week_start"]) if args.get("week_start") else WEEK_START_BASE
    return CmdSpec(argv, env={"WEEK_START": ws})


def _assemble(args: dict) -> CmdSpec:
    n = _issue(args["issue"])
    argv = [PY, "-m", "llm", "assemble",
            "--material-dir", f"{_issue_dir(n)}/material",
            "--weekly-dir", f"{_issue_dir(n)}/weekly",
            "--issue", n]
    if args.get("dry_run"):
        argv.append("--dry-run")
    return CmdSpec(argv)


def _md2html(args: dict) -> CmdSpec:
    if args.get("check"):
        return CmdSpec([PY, "-m", "llm", "md2html", "--check"])
    md = _output_path(args["md_path"])
    argv = [PY, "-m", "llm", "md2html", str(md)]
    if args.get("keep_title"):
        argv.append("--keep-title")
    if args.get("theme"):
        argv += ["--theme", str(args["theme"])]
    return CmdSpec(argv)


def _polish_html(args: dict) -> CmdSpec:
    html = _output_path(args["html_path"])
    return CmdSpec([PY, "-m", "llm.polish_html", str(html)])


def _inline_images(args: dict) -> CmdSpec:
    html = _output_path(args["html_path"])
    return CmdSpec([PY, "-m", "llm.inline_images", str(html)])


def _run_issue(args: dict) -> CmdSpec:
    n = _issue(args["issue"])
    env: dict[str, str] = {"ISSUE": n}
    if args.get("min_score") is not None:
        env["MIN_SCORE"] = str(float(args["min_score"]))
    if args.get("dry_run"):
        env["DRY_RUN"] = "1"
    return CmdSpec(["bash", "run_issue.sh"], env=env)


# ---- 工具注册 ------------------------------------------------------------

def _schema(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object",
            "properties": props,
            "required": required or []}


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict
    build: Tool


TOOLS: dict[str, ToolDef] = {t.name: t for t in [
    ToolDef(
        "status",
        "流水线只读状态：DB 行数 / 最新 run_id / 各期走到哪一步 / 推导 next_step。"
        "任何 agent 起步先跑它，按 next_step 决定下一步。只读、不联网、不写盘。",
        _schema({"issue": {"type": "string", "description": "只看某期号（可选）"},
                 "json": {"type": "boolean", "description": "输出 JSON（默认否）"}}),
        _status),
    ToolDef(
        "preflight",
        "环境自检：提示词校验 + 离线自测 + md2html 就绪 + status 可读。一键确认挂载可用。",
        _schema({}), _preflight),
    ToolDef(
        "capture",
        "每日捕获 PubPeer /api/recent feed（约 400 条），存 pubpeer_id + 元数据。"
        "独立每日操作，run_issue.sh 默认不跑。",
        _schema({"max_offset": {"type": "integer", "description": "feed 偏移上限（默认 400）"}}),
        _capture),
    ToolDef(
        "revisit",
        "回访捕获过文章的完整评论线程（幂等 upsert，可断点续跑）。"
        "7 天后重访更新。",
        _schema({"window": {"type": "string", "description": "'D1 D2' 两个整数，如 '10 3'"},
                 "limit": {"type": "integer", "description": "本次最多回访条数（可分批）"}}),
        _revisit),
    ToolDef(
        "rank",
        "两阶段打分（stage-1 粗筛全部 → 短名单深度回访 stage-2）。"
        "返回分数报告路径（output/issue/<n>/score/<run_id>/）供人工审阅短名单。",
        _schema({"issue": {"type": "string", "description": "期号（正整数或 -1 测试期）"},
                 "window": {"type": "string", "description": "相对窗口 'D1 D2'，如 '10 3'（与 window_dates 互斥）"},
                 "window_dates": {"type": "string",
                                  "description": "绝对窗口 'START END' 两个 ISO 日期（起含止不含）；"
                                                 "不传时正整数期号自动按期号推算"},
                 "window_field": {"type": "string",
                                  "description": "日期基准：captured_at（默认）/ last_commented"},
                 "run_id": {"type": "string", "description": "报告日期，默认今天"}},
                required=["issue"]),
        _rank),
    ToolDef(
        "pick",
        "按分类选稿：此刻才为当期被选论文下载评论图 + 写 manifest。"
        "⚠ 人工门槛：先 dry_run=true 预览（只打印将选的 picks），经人工确认分数线后再 dry_run=false 正式选。",
        _schema({"issue": {"type": "string", "description": "期号"},
                 "min_score": {"type": "number", "description": "分数门槛（默认 0.50，与 run_issue.sh 一致）"},
                 "max_total": {"type": "integer", "description": "每期入选总数上限（默认 25，超出按 final 分降序裁剪；0 = 不限）"},
                 "dry_run": {"type": "boolean", "description": "只预览不下载（默认 false）"}},
                required=["issue"]),
        _pick),
    ToolDef(
        "material",
        "图材整理：每篇三类合并图（first/author/sleuth，每张至多 4 张源图、超限拆 _N），"
        "输出 output/issue/<n>/material/。清场重建（幂等）。",
        _schema({"issue": {"type": "string", "description": "期号"},
                 "max_images": {"type": "integer", "description": "每张合并图最多源图数（默认 4）"}},
                required=["issue"]),
        _material),
    ToolDef(
        "flatten",
        "摊平素材文件夹：扁平 <pid>_<kind>[_N].png → output/issue/<n>/upload/（流水线步骤，亦可供人工手动上传）。",
        _schema({"issue": {"type": "string", "description": "期号"}}, required=["issue"]),
        _flatten),
    ToolDef(
        "generate",
        "单模型提取+写稿（DeepSeek 无多模态，图片描述用评论者原话）。"
        "assemble=true 顺带排版成品 md。⚠ 人工门槛：草稿需人工审核后再 assemble/publish。"
        "自动注入 WEEK_START（期号→起始日+(期号-1)*7）。需 PUBECOSPHERE_LLM_API_KEY。",
        _schema({"issue": {"type": "string", "description": "期号"},
                 "assemble": {"type": "boolean", "description": "写稿后自动排版成品（默认 false）"},
                 "model": {"type": "string", "description": "覆盖默认单模型"},
                 "dry_run": {"type": "boolean", "description": "只打印消息不联网不写盘"},
                 "week_start": {"type": "string", "description": "数据收集基准起始日 YYYY-MM-DD（第 1 期；默认 2026-08-03，期号偏移自动推算）"}},
                required=["issue"]),
        _generate),
    ToolDef(
        "assemble",
        "排版：草稿 → 成品 md + 图片复制（离线）。审完草稿后调。",
        _schema({"issue": {"type": "string", "description": "期号"},
                 "dry_run": {"type": "boolean", "description": "只看告警不写盘"}},
                required=["issue"]),
        _assemble),
    ToolDef(
        "md2html",
        "md → 微信兼容 HTML（仓库内 vendored 转换器，去外部 skill 依赖）。"
        "输出到 md 同目录 .html。",
        _schema({"md_path": {"type": "string",
                             "description": "output/ 下相对路径，如 output/issue/1/weekly/1.md"},
                 "keep_title": {"type": "boolean", "description": "保留主标题（默认 false）"},
                 "theme": {"type": "string", "description": "主题（默认 default）"},
                 "check": {"type": "boolean", "description": "检查 bun/脚本/依赖就绪（无需 md_path）"}}),
        _md2html),
    ToolDef(
        "polish_html",
        "HTML 后处理（确定性幂等）：上下标 sup/sub + 卡片页脚小字紧排 + GitHub 链接修复。原地写回。",
        _schema({"html_path": {"type": "string",
                               "description": "output/ 下 .html 相对路径"}},
                required=["html_path"]),
        _polish_html),
    ToolDef(
        "inline_images",
        "图片 base64 内联（微信手动粘贴专用）：产出 <名>-base64.html；同时清 data-local-path。",
        _schema({"html_path": {"type": "string",
                               "description": "output/ 下 .html 相对路径（通常先 polish_html）"}},
                required=["html_path"]),
        _inline_images),
    ToolDef(
        "run_issue",
        "整期便捷工具：一键跑 run_issue.sh（capture/revisit 可选、数据由长期 cron 爬虫供给、"
        "脚本默认不跑爬虫；rank → pick → material → flatten → "
        "generate → md2html → polish → inline_images）。dry_run=true 只预览 pick 结果。"
        "⚠ 每道人工门槛（分数线/草稿审核/微信推送）仍需人工确认。",
        _schema({"issue": {"type": "string", "description": "期号"},
                 "min_score": {"type": "number", "description": "pick 分数门槛"},
                 "dry_run": {"type": "boolean", "description": "只预览 pick 不下载"}},
                required=["issue"]),
        _run_issue),
]}


# ---- 执行 ----------------------------------------------------------------

_PREFLIGHT_CHECKS: list[tuple[list[str], str]] = [
    ([PY, "-m", "llm", "check"], "提示词校验（两文件八节）"),
    ([PY, "-m", "llm", "selftest"], "离线自测"),
    ([PY, "-m", "llm", "md2html", "--check"], "md2html 就绪（bun+render.ts+node_modules）"),
    ([PY, "-m", "scoring.pipeline", "status", "--json"], "status 只读可跑"),
]


def _run_preflight() -> RunResult:
    lines: list[str] = []
    failed = 0
    for argv, label in _PREFLIGHT_CHECKS:
        r = run_cli(argv)
        mark = "PASS" if r.ok else "FAIL"
        if not r.ok:
            failed += 1
        lines.append(f"[{mark}] {label}（退出 {r.exit_code}）")
        if not r.ok:
            lines.append(r.stderr.strip() or r.stdout.strip())
    lines.append(f"preflight: {'全部通过' if not failed else f'{failed} 项失败'}")
    return RunResult(exit_code=0 if not failed else 1, stdout="\n".join(lines))


def run_tool(name: str, args: dict) -> RunResult:
    """执行工具并返回 RunResult（文本由 mcp_server 包装成 MCP content）。"""
    tool = TOOLS.get(name)
    if not tool:
        return RunResult(exit_code=1, stderr=f"未知工具：{name}")
    if name == "preflight":
        return _run_preflight()
    spec = tool.build(args)
    r = run_cli(spec.argv, env=spec.env)
    return r
