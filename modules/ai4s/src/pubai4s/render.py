"""渲染：post.md → html → base64 内联 html（复用父仓库 llm.md2html / llm.inline_images）。

产出到 md 同目录：
- post.html           md2html 转换（keep-title）
- post-base64.html    图片 base64 内联（浏览器打开 → Ctrl+A → Ctrl+C → 粘贴发布）
"""
from __future__ import annotations

from pathlib import Path

from llm.inline_images import inline_images
from llm.md2html import main as md2html_main


def render_post(md_path: Path, out_dir: Path) -> Path:
    rc = md2html_main([str(md_path), "--keep-title"])
    html_path = md_path.with_suffix(".html")
    if rc != 0 or not html_path.exists():
        raise RuntimeError(
            f"md2html 转换失败（返回 {rc}）。请检查父仓库 vendor 依赖："
            "cd src/vendor/md2html-cli && npx -y bun install")
    html = html_path.read_text(encoding="utf-8")
    new_html, n, missing = inline_images(html, html_path.parent)
    base64_path = out_dir / "post-base64.html"
    base64_path.write_text(new_html, encoding="utf-8")
    if missing:
        print(f"警告：{len(missing)} 张图片缺失，未内联：{missing}")
    return base64_path
