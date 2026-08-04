"""报告渲染：JSON + Markdown 输出到 output/score/<run_id>/。

结果结构（由 pipeline 构建）见 _row_vals 注释；覆盖率来自 cas.coverage。
"""
from __future__ import annotations

import json
from pathlib import Path

_MAJOR_ALIAS = {
    "综合性期刊": "综合", "医学": "医学", "生物学": "生物学", "计算机科学": "计算机科学",
    "材料科学": "材料科学", "化学": "化学", "环境科学与生态学": "环境", "工程技术": "工程技术",
    "物理与天体物理": "物理", "农林科学": "农林", "地球科学": "地球科学", "数学": "数学",
}


def _impact_display(r: dict) -> str:
    if r.get("impact_factor"):
        return f"IF {r['impact_factor']:.1f}"
    if r.get("partition"):
        return f"{r['partition']}区{'·Top' if r.get('top') else ''}"
    if r.get("ccf_grade"):
        return f"CCF {r['ccf_grade']}"
    return "-"


def _breakdown_lines(r: dict) -> list[str]:
    bd = r.get("breakdown") or {}
    lines = []
    for dim, item in sorted(bd.items(), key=lambda kv: -kv[1]["contrib"]):
        lines.append(f"{dim} {item['weight']:.2f}×{item['norm']:.2f}={item['contrib']:.2f}")
    return lines


def render_markdown_table(rows: list[dict], title: str) -> str:
    lines = [f"# {title}", ""]
    lines.append("| # | 标题 | 期刊 | 评论数 | 作者回应 | 评论者数 | 已知打假人 | 激烈交互 | 预警 | 总分 | 链接 |")
    lines.append("|---|------|------|-------|---------|---------|-----------|---------|------|------|------|")
    for i, r in enumerate(rows, 1):
        deep = r.get("deep") or {}
        title_ = (r.get("title") or "")[:60].replace("|", "\\|")
        journal_ = (r.get("journal") or "").replace("|", "\\|")
        if deep:
            resp = "是" if deep.get("author_response") else "否"
            n_users = deep.get("n_users", "-")
            sleuth = "是" if deep.get("sleuth") else "否"
            rounds = deep.get("rounds", 0) or 0
            if deep.get("retraction_eoc"):
                rounds = f"{rounds}（撤稿⚠️）"
        else:
            resp, n_users, sleuth, rounds = "-", "-", "-", "-"
        alert = f"⚠️{r['journal_alert']}" if r.get("journal_alert") else ""
        lines.append(
            f"| {i} | {title_} | {journal_}({_impact_display(r)}) | {r.get('comments_total')} "
            f"| {resp} | {n_users} | {sleuth} | {rounds} | {alert} "
            f"| {r['final']:.2f} | https://pubpeer.com/publications/{r['pubpeer_id']} |"
        )
    lines.append("")
    for i, r in enumerate(rows, 1):
        lines.append(f"**评分明细（#{i}）**：{' · '.join(_breakdown_lines(r))} = **{r['final']:.2f}**")
        lines.append("")
    return "\n".join(lines)


def write_all(out_root: Path, run_id: str, results: list[dict], shortlist: list[dict],
              coverage: list[dict], coverage_md: str) -> Path:
    out = out_root / run_id
    (out / "per_category").mkdir(parents=True, exist_ok=True)

    (out / "all_scores.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "shortlist.json").write_text(
        json.dumps(shortlist, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "coverage.md").write_text(coverage_md, encoding="utf-8")

    # 按分类分组（level=minor 时 category 为小类名，否则为大类）
    by_cat: dict[str, list[dict]] = {}
    for r in results:
        by_cat.setdefault(r.get("category") or r.get("major") or "未分类", []).append(r)

    summary: list[str] = [f"# 周报打分摘要 · {run_id}", "",
                          f"- 文章总数：{len(results)}，短名单：{len(shortlist)}，分类数：{len(by_cat)}", ""]
    for cat in sorted(by_cat, key=lambda c: -len(by_cat[c])):
        rows = sorted(by_cat[cat], key=lambda r: -r["final"])[:20]
        major = rows[0].get("major") or ""
        label = f"{major} / {cat}" if major and major != cat else cat
        title = f"周报 · {label} · {run_id}"
        md = render_markdown_table(rows, title)
        alias = _MAJOR_ALIAS.get(cat, cat or "未分类")
        (out / "per_category" / f"{alias}.md").write_text(md, encoding="utf-8")
        (out / "per_category" / f"{alias}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        summary.append(f"- [{label}](per_category/{alias}.md)：{len(by_cat[cat])} 篇")

    (out / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    return out
