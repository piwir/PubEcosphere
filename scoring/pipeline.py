"""打分系统 CLI：coverage（覆盖率报告）/ rank（两阶段打分选优）。

用法：
    python -m scoring.pipeline coverage --db data/pubpeer.db
    python -m scoring.pipeline rank --db data/pubpeer.db [--stage1-only] [--window 10 3]
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crawler.client import ClientConfig, PubPeerClient
from crawler.store import Store as CrawlerStore

from . import config as config_mod
from . import enrich, report, revisit, score
from .cas import CasIndex, minor_name, minor_names
from .ccf import CcfIndex
from .jcr import JcrIndex
from .signals import extract_comment_signals
from .store import ScoringStore


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_indexes(args):
    cas_idx = CasIndex.build(args.cas, seed_only=args.seed_only)
    jcr_idx = JcrIndex.build(args.jcr)
    ccf_idx = CcfIndex.build(args.ccf, args.ccft, args.alert)
    return cas_idx, jcr_idx, ccf_idx


def _journal_info(cas_idx, jcr_idx, ccf_idx, capture: dict) -> dict:
    cl = cas_idx.classify(capture.get("issn"), capture.get("journal"))
    if_ = jcr_idx.lookup(capture.get("issn"), capture.get("journal"))
    cc = ccf_idx.lookup(capture.get("journal"))
    return {
        "major": cl.major or "未分类",
        "minor": cl.minor,
        "minor_partition": cl.minor_partition,
        "partition": cl.partition,
        "top": cl.top,
        "source": cl.source,
        "method": cl.method,
        "impact_factor": if_,
        "ccf_grade": cc.grade,
        "journal_alert": cc.alert,
    }


def _category_for(jinfo: dict, level: str) -> str:
    """报告分类：level=minor 时用小类中文名（细分）。

    跳过与大类相同/同前缀的小类（如「综合性期刊/综合」），避免大小类重复。
    """
    if level == "minor":
        for name in minor_names(jinfo.get("minor") or ""):
            if name and name != jinfo["major"] and not name.startswith(jinfo["major"]):
                return name
    return jinfo["major"]


_thread_local = threading.local()


def _resolve_doi_worker(cap: dict, pub_doi_map: dict, delay: float) -> tuple:
    """每个工作线程一个独立 client（避免共享节流串行化）。"""
    if not hasattr(_thread_local, "client"):
        _thread_local.client = PubPeerClient(ClientConfig(delay=delay))
    doi, method, ct, cj, match = enrich.resolve_doi(_thread_local.client, cap, pub_doi_map)
    return cap["pubpeer_id"], doi, method, ct, cj, match


def _pub_authors(pub: dict | None) -> list[str]:
    if not pub or not pub.get("authors"):
        return []
    try:
        return json.loads(pub["authors"])
    except (json.JSONDecodeError, TypeError):
        return []


def _in_window(last_commented: str | None, window: tuple[int, int]) -> bool:
    if not last_commented:
        return False
    try:
        dt = datetime.fromisoformat(last_commented.replace("Z", "+00:00"))
    except ValueError:
        return False
    now = datetime.now(timezone.utc)
    lo = now - timedelta(days=window[0])
    hi = now - timedelta(days=window[1])
    return lo <= dt < hi


def _make_shortlist(results: list[dict], k: int) -> list[dict]:
    """每分类（大类或小类）按 stage1(final) 取 top-k。"""
    by_cat: dict[str, list[dict]] = {}
    for r in results:
        by_cat.setdefault(r.get("category") or r["major"], []).append(r)
    out = []
    for cat, rows in by_cat.items():
        out.extend(sorted(rows, key=lambda r: -r["final"])[:k])
    return out


def _coverage_rows(cas_idx, jcr_idx, ccf_idx, captures) -> list[dict]:
    cov = cas_idx.coverage(captures)
    for row in cov:
        row["if"] = jcr_idx.lookup(row["issn"] or None, row["journal"])
        cc = ccf_idx.lookup(row["journal"])
        row["ccf_grade"] = cc.grade
        row["alert"] = cc.alert
        if row["status"] == "unmatched" and cc.grade:
            row["status"] = "ccf"
            row["reason"] = ""
    return cov


def _coverage_md(cov: list[dict]) -> str:
    lines = ["# 期刊覆盖率报告", "",
             "| 期刊 | 文章数 | ISSN | 状态 | 大类 | IF | CCF | 预警 |",
             "|------|-------|------|------|------|----|-----|------|"]
    total_n = sum(r["n"] for r in cov)
    for r in cov:
        if_ = f"{r['if']:.1f}" if r.get("if") else "-"
        jname = r["journal"][:48].replace("|", "\\|")
        lines.append(
            f"| {jname} | {r['n']} | {r['issn'] or '-'} "
            f"| {r['status']} | {r['major'] or '-'} | {if_} | {r['ccf_grade'] or '-'} "
            f"| {r['alert'] or '-'} |")
    lines += ["", f"共 {len(cov)} 个期刊 / {total_n} 篇文章。",
              "", "**大类文章量分布**："]
    from collections import Counter
    ctr = Counter(r["major"] or "未分类" for r in cov for _ in range(r["n"]))
    for major, n in ctr.most_common():
        lines.append(f"- {major}：{n}")
    lines.append("")
    lines.append("**小类（细分）文章量分布**：")
    minor_ctr: Counter = Counter()
    for r in cov:
        for _ in range(r["n"]):
            minor_ctr[r.get("minor_name") or "未细分"] += 1
    for name, n in minor_ctr.most_common(40):
        lines.append(f"- {name}：{n}")
    return "\n".join(lines) + "\n"


# ---- coverage -------------------------------------------------------------

def cmd_coverage(args) -> int:
    cas_idx, jcr_idx, ccf_idx = _build_indexes(args)
    sstore = ScoringStore(args.db)
    captures = sstore.all_captures()
    cov = _coverage_rows(cas_idx, jcr_idx, ccf_idx, captures)

    out_dir = Path(args.output) / "score"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "coverage.json").write_text(json.dumps(cov, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "coverage.md").write_text(_coverage_md(cov), encoding="utf-8")

    print(f"coverage: {len(cov)} journals / {len(captures)} captures")
    print(f"  official={sum(1 for r in cov if r['status']=='official')} "
          f"seed={sum(1 for r in cov if r['status']=='seed')} "
          f"ccf={sum(1 for r in cov if r['status']=='ccf')} "
          f"unmatched={sum(1 for r in cov if r['status']=='unmatched')}")
    print(f"  → {out_dir / 'coverage.md'}")
    for r in cov:
        if r["status"] == "unmatched":
            print(f"    ⚠ {r['journal'][:60]}: {r['reason']}")
    return 0


# ---- rank -----------------------------------------------------------------

def cmd_rank(args) -> int:
    cfg = config_mod.ScoringConfig()
    cas_idx, jcr_idx, ccf_idx = _build_indexes(args)
    sstore = ScoringStore(args.db)
    cstore = CrawlerStore(args.db)
    captures = sstore.all_captures()
    pubs = sstore.all_publications()
    pub_by_pid = {p["pubpeer_id"]: p for p in pubs}
    pub_doi_map = {pid: p.get("doi") for pid, p in pub_by_pid.items() if p.get("doi")}
    client = PubPeerClient(ClientConfig(delay=args.delay))

    # ---- 1. 富集：DOI + v3 ----
    cached_dois = sstore.doi_for([c["pubpeer_id"] for c in captures])
    dois: list[str] = [d for d in cached_dois.values() if d]
    n_resolved = 0
    pending = [c for c in captures
               if args.refresh_enrich or c["pubpeer_id"] not in cached_dois]
    if args.verbose:
        print(f"resolve DOI: {len(pending)} pending (parallel ×{args.enrich_workers}, "
              f"delay {args.delay}s)", flush=True)
    with ThreadPoolExecutor(max_workers=args.enrich_workers) as ex:
        futs = [ex.submit(_resolve_doi_worker, cap, pub_doi_map, args.delay) for cap in pending]
        for fut in as_completed(futs):
            pid, doi, method, ct, cj, match = fut.result()
            sstore.upsert_doi_resolution(pid, doi, method, ct, cj, match)
            if doi:
                dois.append(doi)
                n_resolved += 1
    n_v3 = enrich.fetch_feedback(client, sstore, dois, cfg.enrich_ttl_days, args.verbose)
    if args.verbose:
        print(f"enrich: +{n_resolved} DOI resolved, {n_v3} v3 feedback fetched", flush=True)

    doi_by_pid = sstore.doi_for([c["pubpeer_id"] for c in captures])
    fb_by_pid = {}
    fb_all = sstore.v3_feedback([d for d in dois if d])
    for pid, doi in doi_by_pid.items():
        if doi and doi in fb_all:
            fb_by_pid[pid] = fb_all[doi]

    # ---- 2. stage-1 打分 ----
    results: list[dict] = []
    for cap in captures:
        jinfo = _journal_info(cas_idx, jcr_idx, ccf_idx, cap)
        fb = fb_by_pid.get(cap["pubpeer_id"])
        feats = score.stage1_features(cap, jinfo, fb, cfg, _pub_authors(pub_by_pid.get(cap["pubpeer_id"])))
        s1 = score.apply_category_interest(score.stage1_score(feats, cfg), jinfo["major"], cfg)
        bd = {k: {"weight": cfg.stage1_weights[k], "norm": feats[k],
                  "contrib": cfg.stage1_weights[k] * feats[k]} for k in cfg.stage1_weights}
        results.append({
            "pubpeer_id": cap["pubpeer_id"], "title": cap["title"],
            "journal": cap["journal"], "issn": cap["issn"],
            "last_commented": cap["last_commented"],
            **jinfo, "category": _category_for(jinfo, args.level),
            "stage": 1, "stage1": s1, "stage2": None, "final": s1,
            "comments_total": cap["comments_total"] or 0,
            "v3_comments_total": (fb or {}).get("total_comments"),
            "users": (fb or {}).get("users"),
            "deep": None, "breakdown": bd,
        })

    if args.window:
        before = len(results)
        results = [r for r in results if _in_window(r["last_commented"], args.window)]
        if args.verbose:
            print(f"window {args.window}: {before} → {len(results)}", flush=True)

    # ---- 3. 短名单 + stage-2 深度 ----
    shortlist_rows = _make_shortlist(results, args.max_per_cat)
    if not args.stage1_only:
        ok, skip, n_c = revisit.deep_revisit_shortlist(
            client, cstore, [r["pubpeer_id"] for r in shortlist_rows],
            force=args.force_deep, verbose=args.verbose)
        if args.verbose:
            print(f"deep revisit: {ok} ok, {skip} not-found, {n_c} comments", flush=True)

        spids = [r["pubpeer_id"] for r in shortlist_rows]
        comments_by_pid = sstore.comments_for(spids)
        for r in shortlist_rows:
            pid = r["pubpeer_id"]
            comments = comments_by_pid.get(pid, [])
            sig = extract_comment_signals(comments, cfg.sleuths)
            feats = score.stage2_features(comments, {k: r[k] for k in
                ("partition", "top", "impact_factor", "ccf_grade")}, cfg)
            s2 = score.apply_category_interest(score.stage2_score(feats, cfg), r["major"], cfg)
            r["stage2"] = s2
            r["final"] = score.blend(r["stage1"], s2, cfg)
            r["stage"] = 2
            r["deep"] = sig
            r["comments_total"] = sig["n_comments"]
            r["breakdown"] = {k: {"weight": cfg.stage2_weights[k], "norm": feats[k],
                                  "contrib": cfg.stage2_weights[k] * feats[k]}
                              for k in cfg.stage2_weights}

    # ---- 4. 落库 + 报告 ----
    run_id = args.run_id or datetime.now().strftime("%Y-%m-%d")
    score_rows = [{
        "pubpeer_id": r["pubpeer_id"], "major": r["major"], "minor": r["minor"],
        "minor_partition": r.get("minor_partition") or 0,
        "partition": r["partition"], "top": int(r["top"]),
        "impact_factor": r["impact_factor"], "ccf_grade": r["ccf_grade"],
        "journal_alert": r["journal_alert"], "stage": r["stage"],
        "final_score": r["final"], "breakdown": json.dumps(r["breakdown"], ensure_ascii=False),
    } for r in results]
    sstore.upsert_scores(run_id, score_rows)
    sstore.replace_shortlist(run_id, [{
        "pubpeer_id": r["pubpeer_id"], "major": r["major"],
        "stage1_score": r["stage1"], "stage2_score": r["stage2"],
        "final_score": r["final"],
    } for r in shortlist_rows])

    cov = _coverage_rows(cas_idx, jcr_idx, ccf_idx, captures)
    out = report.write_all(Path(args.output) / "score", run_id, results, shortlist_rows,
                           cov, _coverage_md(cov))
    print(f"rank done: {len(results)} articles, shortlist {len(shortlist_rows)}, run_id={run_id}")
    print(f"  → {out}")
    return 0


# ---- CLI ---------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PubEcosphere 打分系统")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default="data/pubpeer.db")
    common.add_argument("--output", default="output")
    common.add_argument("--cas", default=config_mod.CAS_CSV)
    common.add_argument("--jcr", default=config_mod.JCR_CSV)
    common.add_argument("--ccf", default=config_mod.CCF_CSV)
    common.add_argument("--ccft", default=config_mod.CCFT_CSV)
    common.add_argument("--alert", default=config_mod.ALERT_CSV)
    common.add_argument("--seed-only", action="store_true", help="只用种子映射，忽略官方 CSV")
    common.add_argument("--delay", type=float, default=1.5, help="相邻请求间隔秒数")
    common.add_argument("--enrich-workers", type=int, default=6,
                        help="CrossRef DOI 解析并行线程数（默认 6）")
    common.add_argument("-v", "--verbose", action="store_true")

    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("coverage", parents=[common], help="期刊覆盖率报告（不评分不联网）")
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("rank", parents=[common], help="两阶段打分：粗筛全部 → 短名单深度回访")
    p.add_argument("--run-id", default=None, help="报告日期，默认今天")
    p.add_argument("--level", choices=("major", "minor"), default="minor",
                   help="报告分类粒度：大类或小类（默认小类，细分更接近 HelloGitHub 模式）")
    p.add_argument("--stage1-only", action="store_true", help="只做 stage-1 粗筛，不深度回访")
    p.add_argument("--window", nargs=2, type=int, default=None, metavar=("D1", "D2"),
                   help="硬过滤 last_commented∈[now-D1,now-D2)（默认不过滤）")
    p.add_argument("--max-per-cat", type=int, default=5, help="每大类短名单上限")
    p.add_argument("--refresh-enrich", action="store_true", help="强制刷新 DOI/v3 缓存")
    p.add_argument("--force-deep", action="store_true", help="无视已回访标记，强制深度回访")
    p.set_defaults(func=cmd_rank)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
