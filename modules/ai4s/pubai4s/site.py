"""官网有界爬取：首页起步 → 同域链接全量入队 + 外域仅文档关键词链接入队 → 关键词计分优先爬取。

预算：max_pages 页（默认 8：首页 + 7 子页）、链接索引 ≤200 条、每页文本片段上限。
每页新发现的同域链接合并进 frontier（readthedocs 等每页侧栏都带全量 TOC，去重后天然聚合）。
任何一步失败均降级：首页失败 → 空结果（官网缺失不阻塞主流程），单页失败跳过不重试。
"""
from __future__ import annotations

import heapq
import urllib.parse
from dataclasses import dataclass, field

from .github import GitHubClient, PageData

DOC_KEYWORDS = ("tutorial", "example", "guide", "quickstart", "quick-start",
                "getting-started", "getting_started", "workflow", "notebook",
                "cookbook", "demo", "usage", "documentation")
# 外域入队门槛：只认强文档语义词，防止爬进无关第三方站点
EXTERNAL_DOC_KEYWORDS = ("documentation", "docs", "tutorial", "guide")
MAX_LINKS = 200


def _keyword_score(anchor: str, url: str) -> int:
    blob = (anchor + " " + url).lower()
    return sum(1 for k in DOC_KEYWORDS if k in blob)


@dataclass
class SiteCrawl:
    pages: list[PageData] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)


def crawl_site(client: GitHubClient, start_url: str,
               max_pages: int = 8, homepage_text_limit: int = 8000,
               subpage_text_limit: int = 4000, max_links: int = MAX_LINKS) -> SiteCrawl:
    """从 start_url 起有界爬取：返回 (pages 首页在前, 全站去重链接索引)。首页失败 → 空结果。"""
    home = client.fetch_page(start_url, homepage_text_limit)
    if home is None:
        return SiteCrawl()

    home_netloc = urllib.parse.urlsplit(start_url).netloc.lower()
    pages: list[PageData] = [home]
    crawled: set[str] = {home.url}
    failed: set[str] = set()
    links: list[tuple[str, str]] = []
    seen_links: set[str] = set()
    heap: list[tuple[int, int, str]] = []  # (-score, seq, url)，分数高先爬
    seq = 0

    def _merge(page_links: list[tuple[str, str]]) -> None:
        nonlocal seq
        for anchor, url in page_links:
            if url not in seen_links:
                seen_links.add(url)
                if len(links) < max_links:
                    links.append((anchor, url))
            if url in crawled or url in failed:
                continue
            netloc = urllib.parse.urlsplit(url).netloc
            external = netloc != home_netloc
            if external and not any(k in (anchor + " " + url).lower()
                                    for k in EXTERNAL_DOC_KEYWORDS):
                continue
            heapq.heappush(heap, (-_keyword_score(anchor, url), seq, url))
            seq += 1

    _merge(home.links)
    while heap and len(pages) < max_pages:
        _, _, url = heapq.heappop(heap)
        if url in crawled or url in failed:
            continue
        page = client.fetch_page(url, subpage_text_limit)
        if page is None:
            failed.add(url)
            continue
        crawled.add(url)
        pages.append(page)
        _merge(page.links)
    return SiteCrawl(pages=pages, links=links)
