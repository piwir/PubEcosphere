"""渲染：post.md → html → base64 内联 html（复用父仓库 llm.md2html / llm.inline_images）。

产出到 md 同目录：
- post.html           md2html 转换（keep-title）+ 图注改写
- post-base64.html    图片 base64 内联（浏览器打开 → Ctrl+A → Ctrl+C → 粘贴发布）

图注约定（写稿提示词同步）：图片行 → 空行 → 整行斜体 `*<描述>*`；转换后改写为
自动编号的小字灰字居中段（样式内联，微信粘贴安全）。cover/arch 无图注行自然不匹配。
"""
from __future__ import annotations

import re
from pathlib import Path

from llm.inline_images import inline_images
from llm.md2html import main as md2html_main

# 主形态：图片独立成段，下一段为整行 <em>
_CAPTION_P_RE = re.compile(
    r"(<img[^>]*>\s*</p>)\s*<p[^>]*>\s*<em[^>]*>(.*?)</em>\s*</p>", re.DOTALL)
# 兜底：图片与图注写在同一段（未按约定空行分隔）
_CAPTION_INLINE_RE = re.compile(
    r"(<img[^>]*>[^<]*?)\s*<em[^>]*>(.*?)</em>([^<]*?</p>)", re.DOTALL)
_CAPTION_STYLE = ("text-align:center;font-size:12px;color:#6F6A5E;"
                  "margin:-8px 8px 24px;")

# 兜底：转换器 remark 往返会把正文 `_`/`*`/`[` 转义成 \_ 等，marked 的 URL token
# 不解反转义（URL 残留反斜杠、href 变 %5C）。对非代码段做字面还原（跳过策略与父仓库
# polish_html 的 _TextRewriter 同构）；转换器侧已在 renderer.ts 同步根治，此处防陈旧 dist。
_UNESCAPE_SPLIT_RE = re.compile(
    r"(<code[^>]*>.*?</code>|<pre[^>]*>.*?</pre>"
    r"|<style[^>]*>.*?</style>|<script[^>]*>.*?</script>)", re.DOTALL)


def _unescape_underscores(html: str) -> str:
    parts = _UNESCAPE_SPLIT_RE.split(html)
    for i in range(0, len(parts), 2):
        parts[i] = parts[i].replace("\\_", "_").replace("%5C_", "_")
    return "".join(parts)


def _add_captions(html: str) -> str:
    state = {"n": 0}

    def _standalone(m: re.Match) -> str:
        state["n"] += 1
        return (m.group(1)
                + f'<p class="p" style="{_CAPTION_STYLE}">图{state["n"]}：{m.group(2)}</p>')

    def _inline(m: re.Match) -> str:
        state["n"] += 1
        return (m.group(1) + "</p>"
                + f'<p class="p" style="{_CAPTION_STYLE}">图{state["n"]}：{m.group(2)}</p>'
                + m.group(3))

    html = _CAPTION_P_RE.sub(_standalone, html)
    html = _CAPTION_INLINE_RE.sub(_inline, html)
    return html


def render_post(md_path: Path, out_dir: Path) -> Path:
    rc = md2html_main([str(md_path), "--keep-title"])
    html_path = md_path.with_suffix(".html")
    if rc != 0 or not html_path.exists():
        raise RuntimeError(
            f"md2html 转换失败（返回 {rc}）。请检查父仓库 vendor 依赖："
            "cd src/vendor/md2html-cli && npx -y bun install")
    html = html_path.read_text(encoding="utf-8")
    html = _unescape_underscores(html)
    html = _add_captions(html)
    html_path.write_text(html, encoding="utf-8")  # post.html 与 base64 版保持一致（含图注）
    new_html, n, missing = inline_images(html, html_path.parent)
    base64_path = out_dir / "post-base64.html"
    base64_path.write_text(new_html, encoding="utf-8")
    if missing:
        print(f"警告：{len(missing)} 张图片缺失，未内联：{missing}")
    return base64_path
