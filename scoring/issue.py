"""周报素材打包（pick）：短名单选优后按类别取 picks，收集 md+图片到一期素材夹，并标记已发布。

用法：
    python -m scoring.pipeline pick --db data/pubpeer.db --issue=-1
    # 默认取最近一次 rank 的短名单；--run-id 可指定历史 run；--dry-run 只看选什么

素材目录 output/issue/<issue>/：
    manifest.md / manifest.json     一期素材清单（类别 → picks）
    pub/<pubpeer_id>_files/         该文评论图片 + 完整评论 md（同目录）

选优规则（config）：
    先按选稿门槛过滤：final_score < min_pick_score 不选（低分无推文价值）；
    评论无图片（has_image=0，取自 stage-2 breakdown.images）不选（无图文价值）。
    然后每类候选 >= small_cat_threshold → 取 picks_per_cat（默认 2）篇；
    < 阈值视为小类 → 只取 small_cat_pick（默认 1）篇。
    已在其它期发布过的文章（published 表）一律不再考虑。
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from crawler.client import ClientConfig, PubPeerClient
from crawler.export import render_publication

from . import config as config_mod
from .cas import minor_names
from .store import ScoringStore


def category_for(major: str, minor: str | None) -> str:
    """与 pipeline._category_for 同规则：小类细分去重后作为分类。"""
    if minor:
        for name in minor_names(minor):
            if name and name != major and not name.startswith(major):
                return name
    return major


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _impact_display(r: dict) -> str:
    if r.get("impact_factor"):
        return f"IF {r['impact_factor']:.1f}"
    if r.get("partition"):
        return f"{r['partition']}区" + ("·Top" if r.get("top") else "")
    if r.get("ccf_grade"):
        return f"CCF {r['ccf_grade']}"
    return "-"


def _load_run(sstore: ScoringStore, run_id: str) -> tuple[list[dict], dict[str, dict]]:
    """短名单行（含 scores 的元数据） + 展示字段索引（title/journal/doi/url…）。

    每行附加 `has_image`：stage-2 breakdown.images.norm（0/1，与评论表实际图片引用一致）。
    stage-1 短名单（--stage1-only）无该维度 → None，select_picks 对 None 不过滤。
    """
    cur = sstore.conn.execute(
        """SELECT s.pubpeer_id, s.major, s.minor, s.minor_partition, s.partition, s.top,
                  s.impact_factor, s.ccf_grade, s.journal_alert, s.stage, s.final_score,
                  s.breakdown
           FROM scores s JOIN shortlist sl
             ON sl.run_id = s.run_id AND sl.pubpeer_id = s.pubpeer_id
           WHERE s.run_id = ?
           ORDER BY s.final_score DESC""",
        (run_id,))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        try:
            b = json.loads(r.get("breakdown") or "{}")
        except (json.JSONDecodeError, TypeError):
            b = {}
        img = (b.get("images") or {}).get("norm")
        r["has_image"] = int(img) if img is not None else None
        r.pop("breakdown", None)

    idx: dict[str, dict] = {}
    if rows:
        pids = [r["pubpeer_id"] for r in rows]
        q = ",".join("?" * len(pids))
        for table, wanted in (("captures", ("pubpeer_id", "title", "journal")),
                              ("publications", ("pubpeer_id", "doi", "url", "published_at"))):
            qr = sstore.conn.execute(f"SELECT {','.join(wanted)} FROM {table} WHERE pubpeer_id IN ({q})", pids)
            qcols = [d[0] for d in qr.description]
            for row in qr.fetchall():
                d = dict(zip(qcols, row))
                idx.setdefault(d["pubpeer_id"], {}).update(d)
    return rows, idx


def select_picks(rows: list[dict], published_other: set[str], cfg) -> tuple[list[dict], dict[str, list[dict]]]:
    """按类别选 picks。返回 (picks, grouped)。rows 已按 final 降序。

    先按选稿门槛过滤（config）：
        - final_score < cfg.min_pick_score → 不选（低分无后续推文价值）；
        - 评论无图片（has_image < cfg.min_pick_images）→ 不选；has_image=None（stage-1）不过滤。
    过滤后再按类别分组，小类阈值/每类取篇数规则不变。
    """
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        if r["final_score"] < cfg.min_pick_score:
            continue
        has_img = r.get("has_image")
        if has_img is not None and has_img < cfg.min_pick_images:
            continue
        r["category"] = category_for(r["major"], r["minor"])
        grouped.setdefault(r["category"], []).append(r)
    picks: list[dict] = []
    for cat, cat_rows in grouped.items():
        avail = [r for r in cat_rows if r["pubpeer_id"] not in published_other]
        if not avail:
            continue
        take = cfg.picks_per_cat if len(avail) >= cfg.small_cat_threshold else cfg.small_cat_pick
        picks.extend(avail[:take])
    return picks, grouped


def _manifest_rows(picks: list[dict], meta: dict[str, dict], pubs: dict[str, dict],
                   comments: dict[str, list[dict]], pub_dir: Path) -> list[dict]:
    rows = []
    for p in picks:
        pid = p["pubpeer_id"]
        m = meta.get(pid, {})
        pub = pubs.get(pid) or {}
        files_dir = pub_dir / f"{pid}_files"
        n_img = len([p for p in files_dir.glob("*") if p.suffix.lower() not in (".md",)]) if files_dir.exists() else 0
        rows.append({
            "pubpeer_id": pid,
            "category": p["category"],
            "major": p["major"],
            "title": m.get("title") or pub.get("title"),
            "journal": m.get("journal") or pub.get("journal"),
            "impact": _impact_display(p),
            "final_score": round(p["final_score"], 3),
            "comments_total": pub.get("comments_total"),
            "has_author_response": bool(pub.get("has_author_response")),
            "journal_alert": p.get("journal_alert"),
            "doi": m.get("doi"),
            "url": m.get("url") or f"https://pubpeer.com/publications/{pid}",
            "md": f"pub/{pid}_files/{pid}.md",
            "n_images": n_img,
        })
    return rows


def _manifest_md(issue: str, run_id: str, rows: list[dict]) -> str:
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    lines = [f"# 周报素材 · issue {issue} · run {run_id}", "",
             f"- 素材 {len(rows)} 篇 / {len(by_cat)} 类",
             f"- 生成：{_iso_now()}", ""]
    for cat in sorted(by_cat, key=lambda c: -len(by_cat[c])):
        lines.append(f"## {cat}（{len(by_cat[cat])} 篇）")
        for r in by_cat[cat]:
            resp = "作者回应✓" if r["has_author_response"] else ""
            alert = f" ⚠️{r['journal_alert']}" if r.get("journal_alert") else ""
            lines.append(
                f"- **{r['title'][:80]}** — {r['journal']}（{r['impact']}）· "
                f"{r['final_score']:.2f} 分 · {r['comments_total']} 评论 · {r['n_images']} 图{resp}{alert} · "
                f"[md]({r['md']}) · [PubPeer]({r['url']})")
        lines.append("")
    return "\n".join(lines) + "\n"


def build_issue(sstore: ScoringStore, client: PubPeerClient, run_id: str, issue: str,
                cfg, out_root: Path = Path("output")) -> tuple[list[dict], Path]:
    """选 picks → 渲染 md/图片到 output/issue/<issue>/ → 写清单 → 标记已发布。"""
    rows, meta = _load_run(sstore, run_id)
    published_other = sstore.published_ids(exclude_issue=issue)
    picks, grouped = select_picks(rows, published_other, cfg)
    if not picks:
        raise SystemExit(
            f"no picks for run {run_id}（该 run 文章可能都已发布）——换 --run-id 或清空 published 表")

    issue_dir = out_root / "issue" / str(issue)
    pub_dir = issue_dir / "pub"
    pub_dir.mkdir(parents=True, exist_ok=True)
    src_pub = out_root / "pub"

    pubs = sstore.publications_for([p["pubpeer_id"] for p in picks])
    comments = sstore.comments_for([p["pubpeer_id"] for p in picks])
    picked_rows: list[dict] = []
    published_rows: list[dict] = []
    for p in picks:
        pid = p["pubpeer_id"]
        pub = pubs.get(pid)
        if not pub:
            print(f"  ⚠ {pid}: 无 publication 记录，跳过素材（可能未回访）", flush=True)
            continue
        files_dir = pub_dir / f"{pid}_files"
        files_dir.mkdir(parents=True, exist_ok=True)
        src_files = src_pub / f"{pid}_files"
        if src_files.exists():                       # 复用已本地化的图片，只补缺
            shutil.copytree(src_files, files_dir, dirs_exist_ok=True)
        md = render_publication(client, pub, comments.get(pid, []), files_dir)
        (files_dir / f"{pid}.md").write_text(md, encoding="utf-8")
        picked_rows.append(p)
        published_rows.append({
            "pubpeer_id": pid, "issue": issue, "category": p["category"],
            "final_score": p["final_score"], "picked_at": _iso_now(),
        })
        print(f"  {pid}: {p['category']} · {len(comments.get(pid, []))} 评论", flush=True)

    rows_out = _manifest_rows(picked_rows, meta, pubs, comments, pub_dir)
    (issue_dir / "manifest.json").write_text(
        json.dumps({"issue": issue, "run_id": run_id, "generated_at": _iso_now(),
                    "picks": rows_out}, ensure_ascii=False, indent=1), encoding="utf-8")
    (issue_dir / "manifest.md").write_text(_manifest_md(issue, run_id, rows_out), encoding="utf-8")

    if published_rows:
        sstore.mark_published(published_rows)
    print(f"  manifest: {len(rows_out)} picks / {len(set(r['category'] for r in rows_out))} 类", flush=True)
    return picked_rows, issue_dir


if __name__ == "__main__":
    import argparse
    import sys
    ap = argparse.ArgumentParser(description="周报素材打包（调试入口，正式走 scoring.pipeline pick）")
    ap.add_argument("--db", default="data/pubpeer.db")
    ap.add_argument("--issue", required=True)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = config_mod.ScoringConfig()
    sstore = ScoringStore(args.db)
    run_id = args.run_id or sstore.latest_run_id()
    if args.dry_run:
        rows, _ = _load_run(sstore, run_id)
        picks, grouped = select_picks(rows, sstore.published_ids(exclude_issue=args.issue), cfg)
        print(f"dry-run: run={run_id}, picks {len(picks)} / {len(grouped)} 类")
        for cat in sorted(grouped, key=lambda c: -len(grouped[c])):
            sel = [p for p in picks if p["category"] == cat]
            if sel:
                print(f"  {cat} ({len(grouped[cat])} 候选→取 {len(sel)}): "
                      + ", ".join(p["pubpeer_id"] for p in sel))
        sys.exit(0)
    picks, issue_dir = build_issue(sstore, PubPeerClient(ClientConfig(delay=args.delay)),
                                   run_id, args.issue, cfg)
    print(f"pick done: {len(picks)} articles, issue={args.issue}, run={run_id}")
    print(f"  → {issue_dir}")
