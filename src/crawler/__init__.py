"""爬虫模块：PubPeer 抓取（feed / 文章页 / v3 DOI 反馈）→ SQLite 存储。

纯标准库 urllib（crawler/client.py），浏览器 UA + 限速 + 退避重试。
CLI：python -m crawler.crawl capture|revisit；python -m crawler.export 全量存档。
"""
