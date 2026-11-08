"""单篇回访：抓文章页 → 解析 → 幂等写入 publications/comments → 标记已回访。

**唯一实现**：`crawler.crawl revisit`（批量、按首现窗口取待访）与
`scoring.revisit.deep_revisit_shortlist`（打分短名单深度回访）此前各抄一遍这段
循环，404/异常语义必须保持一致，改一处忘一处就会让两边行为分叉。

调用方只负责「哪些 pid 要访、进度怎么打印」，单条失败一律不中断整体。
"""
from __future__ import annotations

import urllib.error

from . import dates
from .parser import parse_publication_page


def revisit_one(client, store, pubpeer_id: str) -> tuple[str, int]:
    """回访一篇。返回 `(status, detail)`：

        ("ok", 评论条数)        抓到并已写入（publications + comments 幂等 upsert）
        ("not_found", 0)        404：文章已不存在，标记已回访避免反复重试
        ("http_error", 状态码)  其它 HTTP 错误，不改库、下次重试
        ("error", 异常文本)     任何意外错误，不改库、下次重试

    幂等：重复回访同一 pid 只是覆盖同一条 publication、按评论 id upsert，可断点续跑。
    """
    try:
        html = client.publication_page(pubpeer_id)
        pub, comments = parse_publication_page(html)
        at = dates.now_iso()
        store.upsert_publication(pub, at)
        store.upsert_comments(pubpeer_id, comments)
        store.mark_revisited(pubpeer_id, at)
        return "ok", len(comments)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:                     # 文章已不存在，标记跳过
            store.mark_revisited(pubpeer_id, dates.now_iso())
            return "not_found", 0
        return "http_error", exc.code
    except Exception as exc:                    # noqa: BLE001 —— 单条失败不中断整体
        return "error", f"{type(exc).__name__}: {exc}"
