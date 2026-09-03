"""阶段A：抓取仓库 + 官网有界爬取 → 下载图片 → codegraph 代码结构摘要 → LLM 提取结构化材料 md。

流程（与父仓库 pick→material 思路一致）：
1. 抓取仓库元数据 + README + 官网爬取（site.crawl_site：链接索引 + 首页文本 + 子页面摘要，失败不阻塞）；
2. 可选 codegraph 本地索引 → 代码结构摘要（inputs/codegraph.txt；未装/失败自动降级）；
3. 下载候选图片（社交封面/og:image → cover.png，README → img-N.png，爬取页配图每页至多 2 张），
   徽章/图标/SVG 类跳过，失败逐张降级；
4. LLM 按 prompt_extract.md 提取材料 md，末尾程序化追加「已下载图片清单」小节。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from llm.client import LLMClient, LLMError
from llm.config import LLMConfig

from .codegraph import SUMMARY_LIMIT, generate_codegraph_summary
from .github import MAX_IMAGES, GitHubClient, RepoInfo
from .prompts import load_prompt
from .site import SiteCrawl, crawl_site

README_LIMIT = 35000    # README 字符截断（喂给 LLM / 落盘 inputs）
BADGE_RE = ("shields.io", "/badge/", "badgen.net", "img.shields")  # 徽章跳过
# 动态生成/SVG 类"图片"下载后无法当位图内联（渲染失败），与徽章一并跳过
JUNK_IMG_SUFFIX_HOSTS = (".svg", "api.star-history.com", "contrib.rocks")
# 每个爬取页最多贡献 2 张图片进候选（教程页示意图重要，但不让单页霸占配额）
PAGE_IMAGE_CAP = 2

COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


class ExtractionError(RuntimeError):
    pass


@dataclass
class ImageItem:
    filename: str
    alt: str


@dataclass
class FetchResult:
    info: RepoInfo
    readme: str
    website_text: str
    images: list[ImageItem]
    codegraph: str = ""
    crawl_pages: int = 0
    crawl_links: int = 0


def _is_badge(url: str) -> bool:
    return any(b in url for b in BADGE_RE)


def _is_junk_image(url: str) -> bool:
    low = url.lower().split("#", 1)[0]
    return _is_badge(url) or low.endswith(JUNK_IMG_SUFFIX_HOSTS) or any(
        h in low for h in JUNK_IMG_SUFFIX_HOSTS)


def _clean_readme(text: str) -> str:
    """剥 README 噪音：HTML 注释、指向徽章主机的 reference 链接定义、3+ 连续空行。"""
    text = COMMENT_RE.sub("", text)
    text = "\n".join(
        line for line in text.splitlines()
        if not (line.lstrip().startswith("[") and "]:" in line and _is_badge(line))
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def fetch_stage(client: GitHubClient, owner: str, name: str, out_dir: Path,
                use_codegraph: bool = True) -> FetchResult:
    """抓取元数据 + README + 官网有界爬取（如有）+ codegraph 摘要，下载图片，写 inputs/ 与 images.md。"""
    info = client.repo(owner, name)
    readme = _clean_readme(client.readme(owner, name))
    crawl = SiteCrawl()
    if info.homepage:
        crawl = crawl_site(client, info.homepage)
        if not crawl.pages:
            print("警告：官网抓取失败，跳过（不阻塞）")
    home = crawl.pages[0] if crawl.pages else None
    website_text = _website_text(crawl)

    codegraph_text = ""
    if use_codegraph:
        codegraph_text, warnings = generate_codegraph_summary(
            info.owner, info.name, info.default_branch)
        for w in warnings:
            print(f"警告（codegraph）：{w}")

    images = _download_images(client, info, readme, home.og_image if home else "", crawl, out_dir)

    inputs = out_dir / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs / "meta.txt").write_text(
        "\n".join([
            f"名称：{info.name}",
            f"简介：{info.description}",
            f"stars：{info.stars}  forks：{info.forks}  license：{info.license}",
            f"topics：{', '.join(info.topics) if info.topics else '（无）'}",
            f"主页：{info.homepage or '（无）'}",
            f"仓库链接：{info.html_url}",
        ]) + "\n", encoding="utf-8")
    (inputs / "readme.md").write_text(readme[:README_LIMIT], encoding="utf-8")
    if website_text:
        (inputs / "website.txt").write_text(website_text, encoding="utf-8")
    if codegraph_text:
        (inputs / "codegraph.txt").write_text(codegraph_text[:SUMMARY_LIMIT], encoding="utf-8")
    (out_dir / "images.md").write_text(
        "\n".join(f"- ![{i.alt}]({i.filename})" for i in images) + "\n", encoding="utf-8")

    return FetchResult(info=info, readme=readme[:README_LIMIT],
                       website_text=website_text, images=images,
                       codegraph=codegraph_text[:SUMMARY_LIMIT],
                       crawl_pages=len(crawl.pages), crawl_links=len(crawl.links))


def _website_text(crawl: SiteCrawl) -> str:
    """SiteCrawl → 结构化 website.txt 内容：站点链接索引 + 首页文本 + 子页面摘要。"""
    if not crawl.pages:
        return ""
    parts: list[str] = []
    if crawl.links:
        parts.append("## 站点链接索引（锚文本: URL，按发现顺序去重）\n\n"
                     + "\n".join(f"- {a}: {u}" for a, u in crawl.links))
    if crawl.pages[0].text:
        parts.append("## 首页文本\n\n" + crawl.pages[0].text)
    subs = [p for p in crawl.pages[1:] if p.text]
    if subs:
        blocks = "\n\n".join(f"### {p.title or p.url}\n{p.url}\n{p.text}" for p in subs)
        parts.append("## 子页面摘要（按文档关键词优先抓取）\n\n" + blocks)
    return "\n\n".join(parts)


def _download_images(client: GitHubClient, info: RepoInfo, readme: str,
                     og_image: str, crawl: SiteCrawl, out_dir: Path) -> list[ImageItem]:
    images: list[ImageItem] = []
    seen: set[str] = set()

    def _add(url: str, alt: str, filename: str) -> bool:
        if len(images) >= MAX_IMAGES or url in seen or _is_junk_image(url):
            return False
        if client.download(url, out_dir / filename):
            seen.add(url)
            images.append(ImageItem(filename=filename, alt=alt))
            return True
        return False

    cover_src = info.cover_url or og_image
    if cover_src:
        _add(cover_src, "项目封面", "cover.png")
    for alt, url in client.readme_images(info.owner, info.name, info.default_branch, readme):
        _add(url, alt, f"img-{len(images)}.png")
    # 爬取页图片：首页 logo 类多数被 junk 过滤，教程页示意图 alt 带页面标题供写稿匹配
    for idx, page in enumerate(crawl.pages):
        label = "官网配图" if idx == 0 else f"教程页配图（{page.title[:40]}）"
        added = 0
        for url in page.images:
            if added >= PAGE_IMAGE_CAP or len(images) >= 8:
                break
            if _add(url, label, f"img-{len(images)}.png"):
                added += 1
    return images


def build_user_message(info: RepoInfo, readme: str, website_text: str,
                       codegraph: str = "") -> str:
    lines = [
        "## 仓库元数据",
        f"- 名称：{info.name}",
        f"- 简介：{info.description or '（无）'}",
        f"- stars：{info.stars}  forks：{info.forks}  license：{info.license}",
        f"- topics：{', '.join(info.topics) if info.topics else '（无）'}",
        f"- 主页：{info.homepage or '（无）'}",
        f"- 仓库链接：{info.html_url}",
        "",
        "## README 原文",
        readme,
        "",
        "## 官网抓取（结构化：站点链接索引 + 页面文本；抓取失败或未配置主页则为空）",
        website_text or "（无）",
        "",
        "## 代码结构摘要（codegraph 生成）",
        codegraph or "（无）",
    ]
    return "\n".join(lines)


def extract_material(info: RepoInfo, readme: str, website_text: str,
                     codegraph: str = "", model: str | None = None,
                     prompt_dir: str | None = None) -> str:
    """阶段A LLM 调用 → 材料 md 正文（不含图片清单，由调用方追加）。"""
    system = load_prompt("extract", prompt_dir)
    client = LLMClient(LLMConfig.from_env(prompt_dir=prompt_dir))
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": build_user_message(info, readme, website_text, codegraph)},
    ]
    try:
        text = client.chat(messages, model=model, temperature=0.3, max_tokens=None)
    except LLMError as exc:
        raise ExtractionError(f"LLM 调用失败：{exc}") from exc
    text = text.strip()
    if not text:
        raise ExtractionError("模型返回空输出，未生成材料")
    return text


def append_image_manifest(material: str, images: list[ImageItem]) -> str:
    """材料末尾追加「已下载图片清单」小节（写稿模型据此引用本地图片）。"""
    if not images:
        return material
    block = "\n\n## 已下载图片清单\n\n" + "\n".join(
        f"- ![{i.alt}]({i.filename})" for i in images)
    return material.rstrip() + block + "\n"
