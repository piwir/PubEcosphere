"""打分系统 CLI：coverage（覆盖率报告）/ enrich（每日 DOI+v3 预热）/ rank（两阶段打分选优）。

用法：
    python -m scoring.pipeline coverage --db data/pubpeer.db
    python -m scoring.pipeline enrich --db data/pubpeer.db [--limit N]
    python -m scoring.pipeline rank --db data/pubpeer.db [--stage1-only] [--window 10 3]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crawler.client import ClientConfig, PubPeerClient
from crawler.store import Store as CrawlerStore

from . import config as config_mod
from . import enrich, issue, report, revisit, score, status as status_mod
from .cas import CasIndex, minor_name, minor_names
from .ccf import CcfIndex
from .jcr import JcrIndex
from .signals import extract_comment_signals
from .store import ScoringStore


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _score_root(args) -> Path:
    """打分报告目录：给 --issue 时写 output/issue/<期号>/score/，否则写 output/score/。"""
    root = Path(args.output)
    if getattr(args, "issue", None) is not None:
        return root / "issue" / str(args.issue) / "score"
    return root / "score"


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
    """报告分类：level=minor 时用小类中文名（细分），规则与 issue.category_for 一致。"""
    if level == "minor":
        return issue.category_for(jinfo.get("major") or "", jinfo.get("minor"))
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


def _in_window(field_value: str | None, window: tuple[int, int]) -> bool:
    if not field_value:
        return False
    try:
        dt = datetime.fromisoformat(field_value.replace("Z", "+00:00"))
    except ValueError:
        return False
    now = datetime.now(timezone.utc)
    lo = now - timedelta(days=window[0])
    hi = now - timedelta(days=window[1])
    return lo <= dt < hi


def _in_date_range(field_value: str | None, start_iso: str, end_iso: str) -> bool:
    """绝对日期窗口：start <= dt < end（起含止不含，ISO 日期，UTC）。"""
    if not field_value:
        return False
    try:
        dt = datetime.fromisoformat(field_value.replace("Z", "+00:00"))
        lo = datetime.fromisoformat(start_iso)
        hi = datetime.fromisoformat(end_iso)
    except ValueError:
        return False
    if dt.tzinfo is not None:      # 归一到 naive UTC，与 naive 的起止比较
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
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


def _run_enrich(client, sstore, caps: list[dict], pub_doi_map: dict[str, str],
                cfg, args, limit: int | None = None) -> tuple[list[str], int, int]:
    """DOI 解析 + v3 预热（TTL 缓存）。返回 (dois, n_resolved, n_v3)。

    caps 已由调用方过滤（rank 只传窗口内文章）；limit 先截取 caps 再算 pending（enrich 子命令用）。
    """
    if limit:
        caps = caps[:limit]
    cached_dois = sstore.doi_for([c["pubpeer_id"] for c in caps])
    dois: list[str] = [d for d in cached_dois.values() if d]
    n_resolved = 0
    pending = [c for c in caps
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
    return dois, n_resolved, n_v3


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


# ---- status ---------------------------------------------------------------

def cmd_status(args) -> int:
    return status_mod.main(["--db", args.db, "--output", args.output]
                           + ([f"--issue={args.issue}"] if getattr(args, "issue", None) is not None else [])
                           + (["--json"] if getattr(args, "json", False) else []))


# ---- coverage -------------------------------------------------------------

def cmd_coverage(args) -> int:
    cas_idx, jcr_idx, ccf_idx = _build_indexes(args)
    sstore = ScoringStore(args.db)
    captures = sstore.all_captures()
    cov = _coverage_rows(cas_idx, jcr_idx, ccf_idx, captures)

    out_dir = _score_root(args)
    # 只清 coverage 两文件（不误删 rank 的 run 报告目录）
    for name in ("coverage.json", "coverage.md"):
        (out_dir / name).unlink(missing_ok=True)
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


# ---- enrich ---------------------------------------------------------------

def cmd_enrich(args) -> int:
    """每日预热：对全部未缓存文章跑 CrossRef DOI 解析 + v3 拉取（TTL 缓存，rank 时窗口内已命中）。"""
    cfg = config_mod.ScoringConfig()
    sstore = ScoringStore(args.db)
    client = PubPeerClient(ClientConfig(delay=args.delay))
    try:
        captures = sstore.all_captures()
        pubs = sstore.all_publications()
    except Exception as exc:            # noqa: BLE001 —— 无库/无 captures 表时如实报告，退出 0（仿 status）
        print(f"enrich: DB 不可读（{exc}），跳过本轮", file=sys.stderr)
        return 0
    pub_doi_map = {p["pubpeer_id"]: p.get("doi") for p in pubs if p.get("doi")}
    dois, n_resolved, n_v3 = _run_enrich(client, sstore, captures, pub_doi_map, cfg, args,
                                         limit=args.limit)
    print(f"enrich done: {n_resolved} DOI resolved, {n_v3} v3 feedback fetched, "
          f"{len(dois)} cached dois", flush=True)
    return 0


# ---- rank -----------------------------------------------------------------

def cmd_rank(args) -> int:
    cfg = config_mod.ScoringConfig()
    cas_idx, jcr_idx, ccf_idx = _build_indexes(args)
    sstore = ScoringStore(args.db)
    cstore = CrawlerStore(args.db)
    captures = sstore.all_captures()
    if not args.include_published:
        pub_ids = sstore.published_ids()
        if pub_ids:
            before = len(captures)
            captures = [c for c in captures if c["pubpeer_id"] not in pub_ids]
            if args.verbose:
                print(f"exclude published: {before} → {len(captures)}", flush=True)
    pubs = sstore.all_publications()
    pub_by_pid = {p["pubpeer_id"]: p for p in pubs}
    pub_doi_map = {pid: p.get("doi") for pid, p in pub_by_pid.items() if p.get("doi")}
    client = PubPeerClient(ClientConfig(delay=args.delay))

    # ---- 1. 富集：DOI + v3（只对窗口内文章，非窗口信号用不上） ----
    if args.window_dates:
        win_caps = [c for c in captures
                    if _in_date_range(c[args.window_field], args.window_dates[0], args.window_dates[1])]
    elif args.window:
        win_caps = [c for c in captures if _in_window(c[args.window_field], args.window)]
    else:
        win_caps = captures
    if args.verbose:
        print(f"enrich scope: {len(captures)} → {len(win_caps)} window articles", flush=True)

    dois, n_resolved, n_v3 = _run_enrich(client, sstore, win_caps, pub_doi_map, cfg, args)
    if args.verbose:
        print(f"enrich: +{n_resolved} DOI resolved, {n_v3} v3 feedback fetched", flush=True)

    doi_by_pid = sstore.doi_for([c["pubpeer_id"] for c in win_caps])
    fb_by_pid = {}
    fb_all = sstore.v3_feedback([d for d in dois if d])
    for pid, doi in doi_by_pid.items():
        if doi and doi in fb_all:
            fb_by_pid[pid] = fb_all[doi]

    # ---- 2. stage-1 打分 ----
    results: list[dict] = []
    for cap in win_caps:
        jinfo = _journal_info(cas_idx, jcr_idx, ccf_idx, cap)
        fb = fb_by_pid.get(cap["pubpeer_id"])
        feats = score.stage1_features(cap, jinfo, fb, cfg, _pub_authors(pub_by_pid.get(cap["pubpeer_id"])))
        s1 = score.apply_category_interest(score.stage1_score(feats, cfg), jinfo["major"], cfg)
        bd = {k: {"weight": cfg.stage1_weights[k], "norm": feats[k],
                  "contrib": cfg.stage1_weights[k] * feats[k]} for k in cfg.stage1_weights}
        results.append({
            "pubpeer_id": cap["pubpeer_id"], "title": cap["title"],
            "journal": cap["journal"], "issn": cap["issn"],
            "last_commented": cap["last_commented"], "captured_at": cap["captured_at"],
            **jinfo, "category": _category_for(jinfo, args.level),
            "stage": 1, "stage1": s1, "stage2": None, "final": s1,
            "comments_total": cap["comments_total"] or 0,
            "v3_comments_total": (fb or {}).get("total_comments"),
            "users": (fb or {}).get("users"),
            "deep": None, "breakdown": bd,
        })

    if args.window_dates:
        before = len(results)
        results = [r for r in results
                   if _in_date_range(r[args.window_field], args.window_dates[0], args.window_dates[1])]
        if args.verbose:
            print(f"window-dates {args.window_dates[0]}..{args.window_dates[1]} "
                  f"on {args.window_field}: {before} → {len(results)}", flush=True)
    elif args.window:
        before = len(results)
        results = [r for r in results if _in_window(r[args.window_field], args.window)]
        if args.verbose:
            print(f"window {args.window} on {args.window_field}: {before} → {len(results)}", flush=True)

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
    score_root = _score_root(args)
    # rank 自带 coverage 输出：整体清空再写，重跑即全新（scores/shortlist 留在 DB，仅报告刷新）
    shutil.rmtree(score_root, ignore_errors=True)
    out = report.write_all(score_root, run_id, results, shortlist_rows,
                           cov, _coverage_md(cov))
    print(f"rank done: {len(results)} articles, shortlist {len(shortlist_rows)}, run_id={run_id}")
    print(f"  → {out}")
    return 0


# ---- pick -----------------------------------------------------------------

def cmd_pick(args) -> int:
    cfg = config_mod.ScoringConfig()
    if args.picks_per_cat is not None:
        cfg.picks_per_cat = args.picks_per_cat
    if args.small_cat_threshold is not None:
        cfg.small_cat_threshold = args.small_cat_threshold
    if args.small_cat_pick is not None:
        cfg.small_cat_pick = args.small_cat_pick
    if args.min_score is not None:
        cfg.min_pick_score = args.min_score
    if args.min_images is not None:
        cfg.min_pick_images = args.min_images
    if args.max_total is not None:
        cfg.max_picks_total = args.max_total

    sstore = ScoringStore(args.db)
    run_id = args.run_id or sstore.latest_run_id()
    if not run_id:
        print("no run found — 先运行 rank", file=sys.stderr)
        return 1

    if args.dry_run:
        rows, _ = issue._load_run(sstore, run_id)
        picks, grouped = issue.select_picks(rows, sstore.published_ids(exclude_issue=args.issue), cfg)
        print(f"dry-run: run={run_id}, picks {len(picks)} / {len(grouped)} 类", flush=True)
        for cat in sorted(grouped, key=lambda c: -len(grouped[c])):
            sel = [p for p in picks if p["category"] == cat]
            if sel:
                print(f"  {cat} ({len(grouped[cat])} 候选→取 {len(sel)}): "
                      + ", ".join(p["pubpeer_id"] for p in sel), flush=True)
        return 0

    client = PubPeerClient(ClientConfig(delay=args.delay))
    picks, issue_dir = issue.build_issue(sstore, client, run_id, args.issue, cfg, Path(args.output))
    print(f"pick done: {len(picks)} articles, issue={args.issue}, run={run_id}", flush=True)
    print(f"  → {issue_dir}", flush=True)
    return 0


def _non_neg_int(s: str) -> int:
    """argparse type：非负整数（负数会让 --limit/--max-total 等静默截错切片）。"""
    v = int(s)
    if v < 0:
        raise argparse.ArgumentTypeError(f"必须 ≥ 0：{s}")
    return v


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

    p = sub.add_parser("status", parents=[common], help="流水线只读状态：走到哪、下一步是什么（agent 起步工具）")
    p.add_argument("--issue", default=None, help="只看指定期号")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("coverage", parents=[common], help="期刊覆盖率报告（不评分不联网）")
    p.add_argument("--issue", default=None, help="期号标注：报告写到 output/issue/<期号>/score/（不填则写 output/score/）")
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("enrich", parents=[common],
                       help="每日预热：CrossRef DOI 解析 + v3 拉取（TTL 2 天缓存；rank 时窗口内自动命中）")
    p.add_argument("--limit", type=_non_neg_int, default=None, help="本轮最多处理前 N 条捕获（默认全部；测试用）")
    p.add_argument("--refresh-enrich", action="store_true", help="强制刷新 DOI/v3 缓存")
    p.set_defaults(func=cmd_enrich)

    p = sub.add_parser("rank", parents=[common], help="两阶段打分：粗筛全部 → 短名单深度回访")
    p.add_argument("--issue", default=None, help="期号标注：报告写到 output/issue/<期号>/score/（不填则写 output/score/）")
    p.add_argument("--run-id", default=None, help="报告日期，默认今天")
    p.add_argument("--level", choices=("major", "minor"), default="minor",
                   help="报告分类粒度：大类或小类（默认小类，细分更接近 HelloGitHub 模式）")
    p.add_argument("--stage1-only", action="store_true", help="只做 stage-1 粗筛，不深度回访")
    wgroup = p.add_mutually_exclusive_group()
    wgroup.add_argument("--window", nargs=2, type=int, default=None, metavar=("D1", "D2"),
                        help="硬过滤 字段∈[now-D1,now-D2)（默认不过滤）")
    wgroup.add_argument("--window-dates", nargs=2, default=None, metavar=("START", "END"),
                        help="硬过滤 字段∈[START,END)（ISO 日期，起含止不含；按期号选期："
                             "START=WEEK_START+(期号-1)*7，END=START+7 天，与导语标签同算法）")
    p.add_argument("--window-field", choices=("last_commented", "captured_at"),
                   default="last_commented",
                   help="--window 作用的日期字段（默认 last_commented；按首次捕获选期用 captured_at）")
    p.add_argument("--max-per-cat", type=_non_neg_int, default=5, help="每大类短名单上限")
    p.add_argument("--include-published", action="store_true",
                   help="把已发布（published 表）文章也纳入打分（默认排除）")
    p.add_argument("--refresh-enrich", action="store_true", help="强制刷新 DOI/v3 缓存")
    p.add_argument("--force-deep", action="store_true", help="无视已回访标记，强制深度回访")
    p.set_defaults(func=cmd_rank)

    p = sub.add_parser("pick", parents=[common],
                       help="每类选 picks，收集 md+图片到 output/issue/<issue>/，标记已发布")
    p.add_argument("--issue", required=True, metavar="期号", help="期号标注（测试用 -1）")
    p.add_argument("--run-id", default=None, help="打分 run 日期，默认取最新")
    p.add_argument("--picks-per-cat", type=_non_neg_int, default=None, help="每类至多取几篇（默认 2）")
    p.add_argument("--small-cat-threshold", type=_non_neg_int, default=None,
                   help="候选少于多少视为小类（默认 5）")
    p.add_argument("--small-cat-pick", type=_non_neg_int, default=None, help="小类取几篇（默认 1）")
    p.add_argument("--min-score", type=float, default=None,
                   help="最终分低于此值不选（默认 config min_pick_score=0.45）")
    p.add_argument("--min-images", type=_non_neg_int, default=None,
                   help="评论图片少于此值不选（默认 config min_pick_images=1）")
    p.add_argument("--max-total", type=_non_neg_int, default=None,
                   help="每期入选总数上限（默认 config max_picks_total=10；0 = 不限）")
    p.add_argument("--dry-run", action="store_true", help="只打印将选的 picks，不写文件不标记")
    p.set_defaults(func=cmd_pick)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
