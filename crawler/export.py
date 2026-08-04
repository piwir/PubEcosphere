"""导出：把回访所得的文章+评论整理成 Markdown 存档，并本地下载评论图片。

用法：
    python -m crawler.export --db data/pubpeer.db --output output

输出（output/ 已被 .gitignore，只存仓库、不提交）：
    output/pub/<pubpeer_id>.md              一篇文章一个 md，含完整评论线程
    output/pub/<pubpeer_id>_files/          该文评论中引用到的图片（本地副本）

评论 markdown 里的图片链接会被改写为本地相对路径，下载失败时保留原链接。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from .client import ClientConfig, PubPeerClient
from .store import Store

# 评论 markdown 中的图片：![alt](url) 与 <img src="url">
_MD_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_HTML_IMG_RE = re.compile(r'(<img\b[^>]*?src=["\'])([^"\']+)(["\'][^>]*>)', re.IGNORECASE)


def _safe_filename(url: str) -> str:
    """从 URL 取安全的本地文件名。"""
    name = Path(urlparse(url).path).name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    if not name:
        name = f"img_{abs(hash(url)) & 0xFFFFFF}.bin"
    return name


def _download_images(client: PubPeerClient, markdown: str, files_dir: Path) -> str:
    """把评论 markdown 里的远程图片下载到 files_dir，改写为本地相对路径。"""
    files_dir.mkdir(parents=True, exist_ok=True)

    def localize(url: str) -> str | None:
        if not url.startswith(("http://", "https://")):
            return None  # 本地/相对路径不改写
        name = _safe_filename(url)
        dest = files_dir / name
        try:
            if not dest.exists():
                dest.write_bytes(client.get_binary(url))
            return name
        except Exception:  # noqa: BLE001 —— 单张图片失败保留原链接
            return None

    def repl_md(m: re.Match) -> str:
        alt, url = m.group(1), m.group(2).strip()
        name = localize(url)
        return f"![{alt}]({name})" if name else m.group(0)

    def repl_html(m: re.Match) -> str:
        name = localize(m.group(2))
        return f"{m.group(1)}{name}{m.group(3)}" if name else m.group(0)

    md = _MD_IMG_RE.sub(repl_md, markdown)
    md = _HTML_IMG_RE.sub(repl_html, md)
    return md


def render_publication(
    client: PubPeerClient, pub: dict, comments: list[dict], files_dir: Path
) -> str:
    """把一篇文章 + 评论渲染成 Markdown。"""
    pid = pub.get("pubpeer_id")
    title = pub.get("title") or "（无标题）"
    journal = pub.get("journal") or ""
    doi = pub.get("doi") or ""
    pubpeer_url = f"https://pubpeer.com/publications/{pid}"
    src_url = pub.get("url")
    published_at = (pub.get("published_at") or "")[:10]
    fetched_at = (pub.get("fetched_at") or "")[:16].replace("T", " ")
    n_total = pub.get("comments_total") or 0
    has_author = bool(pub.get("has_author_response"))

    try:
        authors = json.loads(pub.get("authors") or "[]")
    except json.JSONDecodeError:
        authors = []

    lines = [
        f"# {title}",
        "",
        f"- 期刊：{journal}" if journal else None,
        f"- DOI：{doi}" if doi else None,
        f"- 作者：{'、'.join(authors[:10])}" + (" 等" if len(authors) > 10 else "") if authors else None,
        f"- 发表时间：{published_at}" if published_at else None,
        f"- 评论总数：{n_total}，作者回应：{'是' if has_author else '否'}",
        f"- 链接：[PubPeer 讨论]({pubpeer_url})",
        f"- 原文：[{src_url}]({src_url})" if src_url and src_url != pubpeer_url else None,
        f"- 抓取时间：{fetched_at}",
        "",
        f"## 评论（{len(comments)} 条）",
        "",
    ]
    lines = [l for l in lines if l is not None]

    for i, c in enumerate(comments, 1):
        who = c.get("user_alias") or "匿名"
        if c.get("user_name"):
            who += f"（{c['user_name']}）"
        tag = " **（作者回应）**" if c.get("is_from_author") else ""
        when = (c.get("accepted_at") or "")[:16].replace("T", " ")
        markdown = _download_images(client, c.get("markdown") or "", files_dir)
        lines += [f"### {i}. {who} · {when}{tag}", "", markdown, ""]

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PubEcosphere 导出：文章+评论 md + 图片本地化")
    ap.add_argument("--db", default="data/pubpeer.db", help="SQLite 数据库路径")
    ap.add_argument("--output", default="output", help="导出目录（其下 pub/ 存放 md 与图片）")
    ap.add_argument("--delay", type=float, default=1.5, help="图片下载请求间隔秒数")
    ap.add_argument("-v", "--verbose", action="store_true", help="逐篇打印进度")
    args = ap.parse_args(argv)

    store = Store(args.db)
    client = PubPeerClient(ClientConfig(delay=args.delay))

    pubs = {p["pubpeer_id"]: p for p in store.all_publications()}
    comments_by_pub: dict[str, list[dict]] = defaultdict(list)
    for row in store.all_comments_with_publications():
        comments_by_pub[row["pubpeer_id"]].append(row)

    out_dir = Path(args.output) / "pub"
    out_dir.mkdir(parents=True, exist_ok=True)

    n_img = 0
    for pid, pub in sorted(pubs.items()):
        files_dir = out_dir / f"{pid}_files"
        md = render_publication(client, pub, comments_by_pub.get(pid, []), files_dir)
        (out_dir / f"{pid}.md").write_text(md, encoding="utf-8")
        n_imgs = len(list(files_dir.glob("*"))) if files_dir.exists() else 0
        n_img += n_imgs
        if args.verbose:
            print(f"  {pid}: {len(comments_by_pub.get(pid, []))} comments, {n_imgs} images", flush=True)

    print(f"export done: {len(pubs)} publications, {sum(len(v) for v in comments_by_pub.values())} comments, "
          f"{n_img} images → {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
