"""流水线只读状态：agent 先跑它自查「走到哪一步、下一步是什么」。

纯只读：不联网、不写 DB、不建表、不建索引、不 import `llm`（避免 .env 导入副作用）。
DB 缺失不算错误（如实报告 db_exists=false），意外异常才返回 1。

用法：
    python -m scoring.pipeline status [--db data/pubpeer.db] [--issue <n>] [--json]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
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
    if not stages["ranked"]:
        next_step = "rank（打分）"
    elif not stages["picked"]:
        next_step = "pick（选稿，先 dry-run 预览给人工批准）"
    elif not stages["materialized"]:
        next_step = "material（图材合并，写回 pub/<pid>_files/）"
    elif not (stages["drafted"] and stages["assembled"]):
        next_step = "generate --assemble（写稿+排版，需 API key；草稿需人工审）"
    elif not stages["html"]:
        next_step = "md2html（md → html）"
    elif not stages["base64"]:
        next_step = "polish_html + inline_images（base64 内联）"
    else:
        next_step = "ready_to_publish（人工浏览器复制粘贴推送）"

    return {
        "issue": issue,
        "exists": issue_dir.is_dir(),
        "dirs": [n for n, ok in dirs.items() if ok],
        "manifest": manifest,
        "stage": stages,
        "next_step": next_step,
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
                            else "该期还没有目录（数据由长期 cron 爬虫供给；直接 rank 打分）")),
    }


def _fmt_issue(s: dict) -> str:
    lines = [
        f"  issue {s['issue']}  dirs={','.join(s['dirs']) or '-'}",
        f"    stage: " + " > ".join(n for n in STAGE_NAMES if s["stage"].get(n)),
        f"    manifest: {s['manifest'] if s['manifest'] else '（无）'}",
        f"    published_in_issue: {s['published_in_issue']}",
        f"    next: {s['next_step']}",
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="流水线只读状态")
    ap.add_argument("--db", default="data/pubpeer.db")
    ap.add_argument("--output", default="output", help="output 根目录（默认 output）")
    ap.add_argument("--issue", default=None, help="只看指定期号")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    try:
        status = collect_status(args.db, issue=args.issue, output_root=args.output)
    except Exception as exc:  # noqa: BLE001 —— 意外错误才退出 1
        print(f"status 失败：{exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        print(_fmt_human(status))
    return 0


if __name__ == "__main__":
    sys.exit(main())
