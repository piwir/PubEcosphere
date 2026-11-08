"""流水线只读状态：agent 先跑它自查「走到哪一步、下一步是什么」。

纯只读：不联网、不写 DB、不建表、不建索引、不 import `llm`（避免 .env 导入副作用）。
DB 缺失不算错误（如实报告 db_exists=false），意外异常才返回 1。

**对外只有一个入口**：`python -m scoring.pipeline status`（本模块只提供
`collect_status` / `render_json` / `_fmt_human`，不再自带 argparse——曾经两个入口
参数各自演化，`--issue=3` 与 `--issue 3` 的支持度不一样）。

用法：
    python -m scoring.pipeline status [--db data/pubpeer.db] [--issue <n>] [--json]
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

STAGE_NAMES = (
    "captured", "ranked", "picked", "materialized",
    "drafted", "assembled", "html", "base64", "ready_to_publish",
)

SCORING_TABLES = ("scores", "shortlist", "published", "v3_feedback", "doi_resolution")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _db_counts(path: str) -> dict:
    """各表行数。表不存在记为 0（不建表）。"""
    out: dict[str, int] = {t: 0 for t in SCORING_TABLES}
    out.update({"captures": 0, "publications": 0, "comments": 0})
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            for t in list(out):
                if t in tables:
                    out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return out
    return out


def _db_max(path: str, table: str, column: str) -> str | None:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if table not in tables:
                return None
            row = conn.execute(f"SELECT MAX({column}) FROM {table}").fetchone()
            return row[0] if row and row[0] else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _latest_run_id(path: str) -> str | None:
    return _db_max(path, "shortlist", "run_id")


def _published_in_issue(path: str, issue: str) -> int:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "published" not in tables:
                return 0
            return conn.execute(
                "SELECT COUNT(*) FROM published WHERE issue=?", (str(issue),)
            ).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def _read_manifest(issue_dir: Path) -> dict | None:
    mf = issue_dir / "manifest.json"
    if not mf.exists():
        return None
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return {
        "issue": data.get("issue"),
        "run_id": data.get("run_id"),
        "generated_at": data.get("generated_at"),
        "n_picks": len(data.get("picks") or []),
    }


def _issue_status(issue_dir: Path, db: str, issue: str) -> dict:
    dirs = {name: (issue_dir / name).is_dir() for name in ("score", "pub", "weekly")}
    score_dir = issue_dir / "score"
    ranked = dirs["score"] and any(p.is_dir() for p in score_dir.iterdir())
    material_index = issue_dir / "pub" / "index.md"
    weekly = issue_dir / "weekly"
    draft = weekly / "stageA_combined.md"
    assembled = weekly / f"{issue}.md"
    html = weekly / f"{issue}.html"
    base64 = weekly / f"{issue}-base64.html"
    manifest = _read_manifest(issue_dir)

    stages = {
        "captured": False,          # 全局捕获状态见 collect_status 顶层
        "ranked": bool(ranked),
        "picked": manifest is not None,
        "materialized": material_index.exists(),
        "drafted": draft.exists(),
        "assembled": assembled.exists(),
        "html": html.exists(),
        "base64": base64.exists(),
        "ready_to_publish": base64.exists(),
    }

    # 下一步推导（generate --assemble 同时产生 drafted + assembled，两者一起判）
    # next_cmd 是可直接复制执行的那条命令（agent 不必再去翻文档拼参数）。
    if not stages["ranked"]:
        step, cmd = "rank（打分）", f"python -m scoring.pipeline rank --db {db} --issue {issue} --window-field captured_at"
    elif not stages["picked"]:
        step, cmd = ("pick（选稿，先 dry-run 预览给人工批准）",
                     f"python -m scoring.pipeline pick --db {db} --issue {issue} --min-score 0.50 --max-total 10 --dry-run")
    elif not stages["materialized"]:
        step, cmd = ("material（图材合并，写回 pub/<pid>_files/）",
                     f"python -m scoring.material --pub-dir {issue_dir}/pub")
    elif not (stages["drafted"] and stages["assembled"]):
        step, cmd = ("写稿（本机首选：agent 按 modules/weekly/prompts/ 自己写）或 "
                     "writer generate（走 API，无头/定时用）",
                     f"python -m writer generate --material-dir {issue_dir}/pub "
                     f"--weekly-dir {issue_dir}/weekly --issue {issue} --assemble")
    elif not stages["html"]:
        step, cmd = "md2html（md → html）", f"python -m llm md2html {issue_dir}/weekly/{issue}.md --theme default --keep-title"
    elif not stages["base64"]:
        step, cmd = ("polish_html + inline_images（base64 内联）",
                     f"python -m writer.polish_html {issue_dir}/weekly/{issue}.html && "
                     f"python -m llm.inline_images {issue_dir}/weekly/{issue}.html")
    else:
        step, cmd = ("ready_to_publish（人工浏览器复制粘贴推送；发布后把链接填进 "
                     "site.json 的 issues[].wechatUrl 再跑 sitejson sync）",
                     f"python -m sitejson sync --issue {issue}")
    next_step, next_cmd = step, cmd

    return {
        "issue": issue,
        "exists": issue_dir.is_dir(),
        "dirs": [n for n, ok in dirs.items() if ok],
        "manifest": manifest,
        "stage": stages,
        "next_step": next_step,
        "next_cmd": next_cmd,
        "published_in_issue": _published_in_issue(db, issue),
    }


def collect_status(db: str, issue: str | None = None, output_root: str = "output") -> dict:
    """汇总流水线状态（只读）。"""
    db_path = Path(db)
    db_exists = db_path.exists()
    counts = _db_counts(db) if db_exists else {t: 0 for t in (
        "captures", "publications", "comments", *SCORING_TABLES)}

    base = Path(output_root) / "issue"
    issues = []
    if base.is_dir():
        for d in sorted(base.iterdir(), key=lambda p: p.name):
            if not d.is_dir() or not d.name.isdigit() and d.name != "-1":
                continue
            if issue is not None and d.name != str(issue):
                continue
            issues.append(_issue_status(d, db, d.name))

    return {
        "db": db,
        "db_exists": db_exists,
        "generated_at": _now_iso(),
        "counts": counts,
        "latest_run_id": _latest_run_id(db) if db_exists else None,
        "last_capture_at": _db_max(db, "captures", "captured_at") if db_exists else None,
        "last_revisit_at": _db_max(db, "captures", "revisited_at") if db_exists else None,
        "captured": bool(db_exists and counts.get("captures", 0) > 0),
        "issues": issues,
        "next_step": ("指定 --issue <n> 查看具体到哪一步" if not issue
                      else (issues[0]["next_step"] if issues
                            else "该期还没有目录（先跑 rank 打分；数据由本地爬虫供给）")),
        "next_cmd": (None if issue is None
                     else (issues[0].get("next_cmd") if issues
                           else f"python -m scoring.pipeline rank --db {db} --issue {issue} --window-field captured_at")),
    }


def _fmt_issue(s: dict) -> str:
    lines = [
        f"  issue {s['issue']}  dirs={','.join(s['dirs']) or '-'}",
        f"    stage: " + " > ".join(n for n in STAGE_NAMES if s["stage"].get(n)),
        f"    manifest: {s['manifest'] if s['manifest'] else '（无）'}",
        f"    published_in_issue: {s['published_in_issue']}",
        f"    next: {s['next_step']}",
        f"    next_cmd: {s.get('next_cmd', '-')}",
    ]
    return "\n".join(lines)


def _fmt_human(status: dict) -> str:
    lines = [
        f"db: {status['db']}  exists={status['db_exists']}",
        f"counts: " + " ".join(f"{k}={v}" for k, v in status["counts"].items()),
        f"latest_run_id: {status['latest_run_id'] or '-'}",
        f"last_capture_at: {status['last_capture_at'] or '-'}",
        f"last_revisit_at: {status['last_revisit_at'] or '-'}",
        f"captured: {status['captured']}",
        f"next_step: {status['next_step']}",
    ]
    for s in status["issues"]:
        lines.append(_fmt_issue(s))
    return "\n".join(lines)


def render(status: dict, as_json: bool = False) -> str:
    """把 `collect_status` 的结果渲染成 CLI 输出文本（JSON 或人类可读）。"""
    if as_json:
        return json.dumps(status, ensure_ascii=False, indent=2)
    return _fmt_human(status)
