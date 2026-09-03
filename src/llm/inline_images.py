"""把 md2html 产出的 html 中的图片转成 base64 内联。

背景：微信手动粘贴必须用 base64 内联。
md2html 产出的 html 图片是相对路径引用，浏览器复制时图片以 URL 引用进剪贴板，
微信服务器拉不到本机 localhost → 粘贴后「载入失败」。转成 data URI 后图片数据本体
进剪贴板，微信直接收下（与截图同理）。

同时清理转换器留在 <img> 上的 `data-local-path` 绝对路径属性（内联后无意义，
且 base64 html 不应携带本机路径）。

用法：python -m llm.inline_images <input.html> [--out <output.html>]
默认输出：<input 去 .html>-base64.html（同目录）。
"""
from __future__ import annotations

import argparse
import base64
import re
from pathlib import Path

_IMG_RE = re.compile(r'<img src="(?P<src>[^"]+)"(?P<rest>[^>]*)>')
_LOCAL_PATH_RE = re.compile(r' data-local-path="[^"]*"')


def inline_images(html: str, base_dir: Path) -> tuple[str, int, list[str]]:
    """把 html 里所有 <img src="本地文件"> 替换成 base64 data URI。

    返回 (新 html, 替换张数, 缺失文件列表)。src 已是 data: 的跳过。
    注意：img 标签含 data-local-path 属性，正则必须带 (?P<rest>[^>]*) 才能匹配，
    只写 src 会替换 0 张。
    """
    missing: list[str] = []

    def _to_data_uri(m: re.Match) -> str:
        src = m.group('src')
        if src.startswith('data:'):
            return m.group(0)
        # 只允许 html 同目录内的相对路径：拒绝 `../`、绝对路径等越界写法，
        # 防止恶意 html 用 <img src="../../.env"> 把本机任意文件 base64 内联进成品
        f = (base_dir / src).resolve()
        if not f.is_relative_to(base_dir.resolve()):
            missing.append(src)
            return m.group(0)
        if not f.is_file():
            missing.append(src)
            return m.group(0)
        b64 = base64.b64encode(f.read_bytes()).decode()
        rest = _LOCAL_PATH_RE.sub("", m.group("rest"))
        return f'<img src="data:image/png;base64,{b64}"{rest}>'

    new_html, n = _IMG_RE.subn(_to_data_uri, html)
    # 兜底：未替换的（data: 跳过 / 缺失未内联）也清掉 data-local-path，不留本机路径
    new_html = _LOCAL_PATH_RE.sub("", new_html)
    return new_html, n, missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m llm.inline_images", description=__doc__)
    parser.add_argument("input", type=Path, help="md2html 产出的 html")
    parser.add_argument("--out", type=Path, default=None,
                        help="输出路径（默认 <input>-base64.html，与 input 同目录）")
    args = parser.parse_args(argv)

    src = Path(args.input)
    if not src.exists():
        print(f"输入不存在：{src}", file=__import__("sys").stderr)
        return 1
    out = args.out or src.with_name(src.stem + "-base64.html")

    html = src.read_text(encoding="utf-8")
    new_html, n, missing = inline_images(html, src.parent)
    out.write_text(new_html, encoding="utf-8")
    print(f"替换 {n} 张图 → {out} ({out.stat().st_size / 1024 / 1024:.1f} MB)")
    if missing:
        print(f"警告：{len(missing)} 张缺失，未内联：{missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
