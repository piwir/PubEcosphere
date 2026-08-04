"""深度回访：对显式短名单 pubpeer_id 抓完整评论线程。

复用 crawler 的 client/parser/store，镜像 crawl.cmd_revisit 的
404/瞬时错误跳过语义；已回访过的文章默认跳过（--force 则重访）。
"""
from __future__ import annotations

import urllib.error

from crawler import dates
from crawler.parser import parse_publication_page


def deep_revisit_shortlist(client, store, pubpeer_ids: list[str],
                           force: bool = False, verbose: bool = False) -> tuple[int, int, int]:
    """回访短名单。返回 (ok, not_found_skip, comments)。"""
    already = {p["pubpeer_id"] for p in store.all_publications()}
    wanted = set(pubpeer_ids)
    pending = [p for p in pubpeer_ids if force or p not in already]
    if verbose:
        print(f"revisit: shortlist {len(wanted)}, already-revisited {len(wanted & already)}, "
              f"pending {len(pending)}", flush=True)

    n_ok = n_skip = n_comments = 0
    for pid in pending:
        try:
            html = client.publication_page(pid)
            pub, comments = parse_publication_page(html)
            store.upsert_publication(pub, dates.now_iso())
            store.upsert_comments(pid, comments)
            store.mark_revisited(pid, dates.now_iso())
            n_ok += 1
            n_comments += len(comments)
            if verbose:
                print(f"  {pid}: {len(comments)} comments", flush=True)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                store.mark_revisited(pid, dates.now_iso())
                n_skip += 1
            else:
                print(f"  {pid}: HTTP {exc.code}, skipped", flush=True)
        except Exception as exc:        # noqa: BLE001 —— 单条失败不中断
            print(f"  {pid}: error {exc}, skipped", flush=True)
    return n_ok, n_skip, n_comments
