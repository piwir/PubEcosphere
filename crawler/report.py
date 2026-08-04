"""周报生成：按评论时间过滤 [now-10d, now-3d] 窗口，输出 markdown。

用法：
    python3 -m crawler.report [--start-days 10] [--end-days 3] [--output output/]

输出文件：output/weekly_<start>_<end>.md
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from . import dates
from .store import Store


def _fmt_iso(iso: str | None) -> str:
    return (iso or "未知时间")[:16].replace("T", " ")


def _authors(rows: list[dict]) -> list[str]:
    raw = rows[0].get("pub_authors")
    try:
        return json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return []


def build_report(store: Store, lo: str, hi: str) -> str:
    """lo/hi 为 ISO 字符串，闭区间 [lo, hi]。"""
    rows = store.all_comments_with_publications()
    # 按窗口过滤（Python 侧比较，避免时区/格式差异）
    in_window = [
        r for r in rows
        if lo <= (r["accepted_at"] or "") <= hi
    ]
    by_pub: dict[str, list[dict]] = defaultdict(list)
    for r in in_window:
        by_pub[r["pubpeer_id"]].append(r)

    # 按窗口内评论数降序
    ordered = sorted(by_pub.items(), key=lambda kv: -len(kv[1]))

    lines = [
        "# PubEcosphere 周报",
        "",
        f"- 窗口：`{_fmt_iso(lo)}` ~ `{_fmt_iso(hi)}`",
        f"- 涉及文章：{len(ordered)} 篇，窗口内评论：{len(in_window)} 条",
        "",
    ]

    for idx, (pid, comments) in enumerate(ordered, 1):
        r0 = comments[0]
        title = r0.get("pub_title") or "（无标题）"
        journal = r0.get("pub_journal") or ""
        doi = r0.get("pub_doi") or ""
        authors = _authors(comments)
        pubpeer_url = f"https://pubpeer.com/publications/{pid}"
        src_url = r0.get("pub_url")      # data-publication 里的原文链接（如 PubMed）
        n_author = sum(1 for c in comments if c["is_from_author"])
        comments.sort(key=lambda c: c["accepted_at"] or "")

        lines.append(f"## {idx}. {title}")
        lines.append("")
        if journal:
            lines.append(f"- 期刊：{journal}")
        if doi:
            lines.append(f"- DOI：{doi}")
        if authors:
            lines.append(f"- 作者：{'、'.join(authors[:6])}" + (" 等" if len(authors) > 6 else ""))
        lines.append(f"- 窗口内评论：{len(comments)} 条（含作者回应 {n_author} 条）")
        lines.append(f"- 链接：[PubPeer 讨论]({pubpeer_url})")
        if src_url and src_url != pubpeer_url:
            lines.append(f"- 原文：[{src_url}]({src_url})")
        lines.append("")

        for c in comments:
            who = c["user_alias"] or "匿名"
            if c["user_name"]:
                who += f"（{c['user_name']}）"
            tag = " **（作者回应）**" if c["is_from_author"] else ""
            lines.append(f"> **{who}** · {_fmt_iso(c['accepted_at'])}{tag}")
            lines.append(">")
            content = (c["markdown"] or "").strip()
            for para in content.splitlines():
                if para.strip():
                    lines.append(f"> {para}")
            lines.append("")

    lines.append("---")
    lines.append("*本报告由 PubEcosphere 自动生成，仅供学术诚信研究参考。*")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PubEcosphere 周报生成")
    ap.add_argument("--db", default="data/pubpeer.db", help="SQLite 数据库路径")
    ap.add_argument("--start-days", type=int, default=10, help="窗口起点：N 天前")
    ap.add_argument("--end-days", type=int, default=3, help="窗口终点：N 天前")
    ap.add_argument("--output", default="output", help="markdown 输出目录")
    args = ap.parse_args(argv)

    now = dates.utcnow()
    lo = dates.to_iso(now - timedelta(days=args.start_days))
    hi = dates.to_iso(now - timedelta(days=args.end_days))

    md = build_report(Store(args.db), lo, hi)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"weekly_{lo[:10]}_{hi[:10]}.md"
    out_file.write_text(md, encoding="utf-8")
    print(f"report written: {out_file}")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
