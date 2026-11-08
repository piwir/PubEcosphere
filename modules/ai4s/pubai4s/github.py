"""GitHub REST + 网页抓取（纯 stdlib urllib）：仓库元数据、README 原文、官网文本与图片。

限速与重试复刻 src/crawler/client.py 的模式：瞬时错误退避重试；
未认证 API 60 req/h，403/429 时给出提示。404 → RepoNotFoundError。
"""
from __future__ import annotations

import html as html_mod
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

API = "https://api.github.com"
RETRYABLE = {429, 500, 502, 503, 504}  # 瞬时错误，可退避重试
RATE_LIMIT_HINT = "GitHub 未认证 API 限速 60 req/h，请稍后再试（或设置 GITHUB_TOKEN 提升限额）"

UA = "Mozilla/5.0 (X11; Linux x86_64) pubai4s/0.1"
README_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
HTML_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
HTML_ATTR_RE = re.compile(r"([a-zA-Z-]+)\s*=\s*[\"']([^\"']*)[\"']")
MAX_IMAGES = 8  # 下载图片总数上限（含封面）


class RepoNotFoundError(RuntimeError):
    """仓库不存在或无权访问（404）。"""


class RateLimitedError(RuntimeError):
    """触发 GitHub 限速（403 无配额 / 429）。"""


@dataclass
class PageData:
    """单页抓取结果：标题、纯文本、og:image、绝对化图片 URL、规范化链接。"""

    url: str
    title: str
    text: str
    og_image: str
    images: list[str]
    links: list[tuple[str, str]]  # (锚文本, 绝对 URL)


@dataclass
class RepoInfo:
    owner: str
    name: str
    description: str
    stars: int
    forks: int
    license: str
    default_branch: str
    html_url: str
    homepage: str  # 官网，可为空串
    topics: list[str]  # GitHub topics，可为空列表
    cover_url: str  # social_preview_image，可为空串


@dataclass
class GitHubClientConfig:
    timeout: float = 30.0
    max_retries: int = 3
    backoff: float = 5.0


class _TextParser(HTMLParser):
    """去 script/style 的纯文本提取 + title/og:image/图片/链接收集（URL 全部解析为绝对地址）。

    两级跳过：script/style/noscript 内**全跳**（文本、链接、图片都不收）；
    nav/header/footer/aside 内**只跳文本**（导航噪音不进正文，但链接与图片照常收集——
    readthedocs 等站点的教程索引正来自侧栏 <nav> 里的 <a>）。
    链接按 URL 去重、剥资源后缀；图片仅解析绝对 URL，不做后缀过滤。
    """

    LINK_ASSET_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
                         ".pdf", ".zip", ".tar.gz", ".ico", ".css", ".js")
    TEXT_SKIP_TAGS = ("nav", "header", "footer", "aside")

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base = base_url
        self._skip = 0
        self._nav_skip = 0
        self.chunks: list[str] = []
        self.images: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._seen_links: set[str] = set()
        self.og_image = ""
        self.title = ""
        self._in_title = 0
        self._anchor_buf: Optional[list[str]] = None
        self._anchor_href = ""

    def _handle_skip(self, tag: str) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag in self.TEXT_SKIP_TAGS:
            self._nav_skip += 1

    def _abs_url(self, raw: str) -> str:
        raw = raw.strip().split("#", 1)[0]
        if not raw or raw.startswith(("data:", "mailto:", "tel:", "javascript:")):
            return ""
        try:
            p = urllib.parse.urlsplit(urllib.parse.urljoin(self.base, raw))
        except ValueError:
            return ""
        if p.scheme not in ("http", "https") or not p.netloc:
            return ""
        return urllib.parse.urlunsplit(
            (p.scheme, p.netloc.lower(), p.path.rstrip("/") or "/", p.query, ""))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self._handle_skip(tag)
        attrs = dict(attrs or {})
        if tag == "title":
            self._in_title += 1
        elif tag == "meta" and (attrs.get("property") == "og:image" or attrs.get("name") == "og:image"):
            self.og_image = attrs.get("content", "")
        elif tag == "img" and attrs.get("src"):
            url = self._abs_url(attrs["src"])
            if url:
                self.images.append(url)
        elif tag == "a" and attrs.get("href") and self._skip == 0:
            self._anchor_buf = []
            self._anchor_href = attrs["href"]

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._skip > 0:
            self._skip -= 1
        elif tag in self.TEXT_SKIP_TAGS and self._nav_skip > 0:
            self._nav_skip -= 1
        if tag == "title" and self._in_title:
            self._in_title -= 1
        if tag == "a" and self._anchor_buf is not None:
            text = " ".join("".join(self._anchor_buf).split())
            url = self._abs_url(self._anchor_href)
            if (text and url and self._skip == 0
                    and not url.lower().split("?")[0].endswith(self.LINK_ASSET_SUFFIX)
                    and url not in self._seen_links):
                self._seen_links.add(url)
                self.links.append((text[:80], url))
            self._anchor_buf = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._anchor_buf is not None:
            # 锚文本不受 nav 跳过影响：侧栏 TOC 的教程标题正来自 nav 内 <a> 的文本
            self._anchor_buf.append(data)
        if self._nav_skip:
            return
        if self._in_title:
            self.title += data
        self.chunks.append(data)

    def text(self) -> str:
        parts = [html_mod.unescape(c).strip() for c in self.chunks]
        lines = [l for l in parts if l]
        return "\n".join(lines)


class GitHubClient:
    def __init__(self, token: str = "", config: Optional[GitHubClientConfig] = None):
        self.token = token
        self.config = config or GitHubClientConfig()

    def _request(self, url: str, accept: str) -> bytes:
        last_exc: Optional[Exception] = None
        for attempt in range(self.config.max_retries + 1):
            headers = {"Accept": accept, "User-Agent": UA}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                    return resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise RepoNotFoundError(f"仓库不存在或无权访问：{url}") from exc
                if exc.code in (403, 429):
                    raise RateLimitedError(RATE_LIMIT_HINT) from exc
                if exc.code in RETRYABLE and attempt < self.config.max_retries:
                    last_exc = exc
                    time.sleep(self.config.backoff * (attempt + 1))
                    continue
                raise
            except (urllib.error.URLError, OSError) as exc:
                last_exc = exc
                if attempt < self.config.max_retries:
                    time.sleep(self.config.backoff * (attempt + 1))
                    continue
                break
        raise RuntimeError(f"request failed after retries: {url}") from last_exc

    def repo(self, owner: str, name: str) -> RepoInfo:
        data = json.loads(self._request(f"{API}/repos/{owner}/{name}", accept="application/vnd.github+json").decode("utf-8"))
        return RepoInfo(
            owner=data["owner"]["login"],
            name=data["name"],
            description=data.get("description") or "",
            stars=data.get("stargazers_count", 0),
            forks=data.get("forks_count", 0),
            license=(data.get("license") or {}).get("spdx_id", "N/A") or "N/A",
            default_branch=data.get("default_branch", "main"),
            html_url=data["html_url"],
            homepage=data.get("homepage") or "",
            topics=data.get("topics") or [],
            cover_url=data.get("social_preview_image") or "",
        )

    def readme(self, owner: str, name: str) -> str:
        """README 原文（/readme 自动选默认分支；Accept: raw 直接返回文件内容）。"""
        raw = self._request(
            f"{API}/repos/{owner}/{name}/readme",
            accept="application/vnd.github.raw+json",
        )
        return raw.decode("utf-8", errors="replace")

    def fetch_page(self, url: str, text_limit: int = 8000) -> Optional[PageData]:
        """抓取单页：返回 PageData；失败（连不上/非 HTML/URL 畸形/解析崩）返回 None，不抛异常。"""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                if "html" not in resp.headers.get("Content-Type", "").lower():
                    return None
                raw = resp.read(1_000_000)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
            return None
        parser = _TextParser(url)
        try:
            parser.feed(raw.decode("utf-8", errors="replace"))
        except Exception:
            return None
        return PageData(url=url, title=parser.title.strip()[:120],
                        text=parser.text()[:text_limit], og_image=parser.og_image,
                        images=parser.images, links=parser.links)

    def download(self, url: str, dest: Path, max_bytes: int = 20 * 1024 * 1024) -> bool:
        """下载任意 URL 到 dest（封面图等，非 API 请求）。失败返回 False，不抛异常。

        流式读取并有 max_bytes 上限：图片来源是任意仓库的 README/爬取页（不可信输入），
        防超大文件打爆内存/磁盘（下载结果还会经 base64 内联二次放大）。
        """
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                total = 0
                chunks: list[bytes] = []
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        return False
                    chunks.append(chunk)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            return False
        data = b"".join(chunks)
        if not data:
            return False
        dest.write_bytes(data)
        return True

    # -- 图片候选收集 -------------------------------------------------------

    def readme_images(self, owner: str, name: str, branch: str, readme: str) -> list[tuple[str, str]]:
        """README 里的图片引用（markdown `![]()` 与 HTML `<img src>`）→ [(alt, 可下载 URL)]，
        相对路径解析到 raw.githubusercontent，URL fragment（如 #gh-light-mode-only）剥离；
        HTML img 带 width/height 纯数字且 ≤64 判定为图标（致谢/账号图标），跳过。"""
        out: list[tuple[str, str]] = []
        for alt, src in README_IMG_RE.findall(readme):
            url = self._resolve_src(src, owner, name, branch)
            if url:
                out.append((alt.strip() or "README 配图", url))
        for tag in HTML_IMG_TAG_RE.findall(readme):
            attrs = dict(HTML_ATTR_RE.findall(tag))
            w, h = attrs.get("width", ""), attrs.get("height", "")
            if (w.isdigit() and int(w) <= 64) or (h.isdigit() and int(h) <= 64):
                continue
            url = self._resolve_src(attrs.get("src", ""), owner, name, branch)
            if url:
                out.append(("README 配图", url))
        return out

    def _resolve_src(self, src: str, owner: str, name: str, branch: str) -> str:
        src = src.strip().split("#", 1)[0].strip()
        if not src or src.startswith(("data:", "mailto:")):
            return ""
        if src.startswith("//"):
            return "https:" + src
        if src.startswith(("http://", "https://")):
            # github.com/<owner>/<name>/blob/<branch>/<path> → raw
            m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$", src)
            if m:
                return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}/{m.group(4)}"
            return src
        # 相对路径 → 仓库根下的文件
        path = src.lstrip("./")
        return f"https://raw.githubusercontent.com/{owner}/{name}/{branch}/{path}"
