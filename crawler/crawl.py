"""爬取主入口：捕获 feed + 延迟回访文章评论。

用法：
    # 每日捕获（服务器 cron）：记录 /api/recent/from/{0..400} 的 pubpeer_id
    python3 -m crawler.crawl capture

    # 回访：抓捕获过的文章页，提取完整评论（--limit 可分批续跑）
    python3 -m crawler.crawl revisit --limit 50

设计：
    feed 只能看到最近约 3 天评论动态 → 捕获记录 pubpeer_id；
    7 天后这些文章的评论恰好落在 [now-10d, now-3d] 周报窗口 → 回访抓评论。
"""
from __future__ import annotations

import argparse
import sys
import urllib.error
from datetime import timedelta

from . import dates
from .client import ClientConfig, PubPeerClient
from .parser import parse_feed_item, parse_publication_page
from .store import Store


def cmd_capture(args: argparse.Namespace, client: PubPeerClient, store: Store) -> int:
    now = dates.now_iso()
    total = 0
    pages = 0
    offset = 0
    while offset <= args.max_offset:
        try:
            items = client.recent_feed(offset)
        except urllib.error.HTTPError as exc:
            if exc.code == 403:     # 超过 400 条上限
                break
            raise
        if not items:
            break
        store.upsert_captures([parse_feed_item(i) for i in items], now)
        total += len(items)
        pages += 1
        if args.verbose:
            print(f"  page {offset}: +{len(items)} items", flush=True)
        offset += 40
    print(f"capture done: {pages} pages, {total} items recorded at {now}", flush=True)
    return total


def cmd_revisit(args: argparse.Namespace, client: PubPeerClient, store: Store) -> int:
    now = dates.utcnow()
    since = dates.to_iso(now - timedelta(days=args.revisit_days))
    pending = store.captures_for_revisit(
        since=since, limit=args.limit, force=args.force, min_comments=args.min_comments,
    )
    print(f"revisit: {len(pending)} captures to revisit (since {since})", flush=True)

    n_ok = n_skip = n_comments = 0
    for rec in pending:
        pid = rec["pubpeer_id"]
        try:
            html = client.publication_page(pid)
            pub, comments = parse_publication_page(html)
            store.upsert_publication(pub, dates.now_iso())
            store.upsert_comments(pid, comments)
            store.mark_revisited(pid, dates.now_iso())
            n_ok += 1
            n_comments += len(comments)
            if args.verbose:
                print(f"  {pid}: {len(comments)} comments", flush=True)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:     # 文章已不存在，标记跳过
                store.mark_revisited(pid, dates.now_iso())
                n_skip += 1
            else:
                print(f"  {pid}: HTTP {exc.code}, skipped", flush=True)
        except Exception as exc:    # noqa: BLE001 —— 单条失败不中断整体
            print(f"  {pid}: error {exc}, skipped", flush=True)
    print(f"revisit done: {n_ok} ok, {n_skip} not-found, {n_comments} comments stored", flush=True)
    return n_ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PubEcosphere 爬虫：捕获 feed + 延迟回访")
    ap.add_argument("--db", default="data/pubpeer.db", help="SQLite 数据库路径")
    ap.add_argument("--delay", type=float, default=1.5, help="相邻请求间隔秒数")
    ap.add_argument("-v", "--verbose", action="store_true", help="逐条打印进度")

    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("capture", help="捕获 /api/recent feed（记录 pubpeer_id）")
    p.add_argument("--max-offset", type=int, default=400, help="feed 偏移上限（默认400）")

    p = sub.add_parser("revisit", help="回访捕获过的文章页，提取评论")
    p.add_argument("--revisit-days", type=int, default=7,
                   help="回访 >=N 天前捕获的文章（默认7）")
    p.add_argument("--limit", type=int, default=None, help="本次最多回访条数（可分批续跑）")
    p.add_argument("--min-comments", type=int, default=0,
                   help="只回访评论数 >=N 的文章（默认0不过滤）")
    p.add_argument("--force", action="store_true", help="无视 7 天内已回访，全部重访")

    args = ap.parse_args(argv)
    store = Store(args.db)
    client = PubPeerClient(ClientConfig(delay=args.delay))

    if args.cmd == "capture":
        cmd_capture(args, client, store)
    elif args.cmd == "revisit":
        cmd_revisit(args, client, store)

    print("stats:", store.stats(), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
