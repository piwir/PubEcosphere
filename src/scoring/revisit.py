"""深度回访：对显式短名单 pubpeer_id 抓完整评论线程（stage-2 打分前）。

单篇抓取/写入逻辑与 `crawler.crawl revisit` 共用 `crawler.revisit.revisit_one`
（404/瞬时错误跳过语义只有一份实现）；本模块只负责「短名单里哪些还没访过」。
"""
from __future__ import annotations

from crawler.revisit import revisit_one


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
        status, detail = revisit_one(client, store, pid)
        if status == "ok":
            n_ok += 1
            n_comments += detail
            if verbose:
                print(f"  {pid}: {detail} comments", flush=True)
        elif status == "not_found":
            n_skip += 1
        elif status == "http_error":
            print(f"  {pid}: HTTP {detail}, skipped", flush=True)
        else:
            print(f"  {pid}: error {detail}, skipped", flush=True)
    return n_ok, n_skip, n_comments
