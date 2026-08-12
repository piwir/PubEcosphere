"""baoyu-markdown-to-html 产出的周报 HTML 后处理（确定性、幂等，不依赖 LLM）：

1. 数学上下标：把 LaTeX 残记 `^()`/`_x` 转成 <sup>/<sub>（微信不认 KaTeX）。
   `Foxp3^(DTR-GFP/y)` → `Foxp3<sup>DTR-GFP/y</sup>`；`Sum(w_1,...,w_n)` → `w<sub>1</sub>`。
2. 卡片页脚链接：每张卡片末尾「PubPeer 讨论 + DOI」两行 blockquote 的 <p> 注入
   与英文标题一致的 小字紧排（`font-size: calc(Npx * 0.85)`、`line-height: 1.3`）。

页脚链接为什么走后处理而非 CSS：blockquote 位于 `**现状**` 段落之后（结构是
`<p><strong>现状</strong>…</p><blockquote>…`），没有 `h3 + blockquote` 那样的相邻
选择器可命中，juice 又不支持 `:has()`；且微信只保留内联 style → 直接给该 <p> 注入
内联样式（幂等：先清掉已有 font-size/line-height 再加新值）。

用法：python -m llm.polish_html <input.html> [--out <output.html>]
默认原地写回（同 run_issue.sh 里 GitHub 链接修复的写法）。
"""
from __future__ import annotations

import argparse
import re
from html.parser import HTMLParser
from pathlib import Path

SUP_STYLE = "font-size:75%; vertical-align:super; line-height:1;"
SUB_STYLE = "font-size:75%; vertical-align:sub; line-height:1;"

_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+")

# 上标：带括号（去括号）→ 数字 → 字母/词
_SUP_PAREN = re.compile(r"\^\(([^()]*)\)")
_SUP_NUM = re.compile(r"\^([-+]?\d+(?:\.\d+)?)")
_SUP_WORD = re.compile(r"\^([A-Za-z][A-Za-z0-9]*)")
# 下标：花括号 → 单字符（保守，避免散文里 _ 分隔误伤）
_SUB_BRACE = re.compile(r"_\{([^{}]*)\}")
_SUB_CHAR = re.compile(r"_([A-Za-z0-9])")


def _protect(text: str) -> tuple[str, list[str]]:
    """把 URL/DOI 换成不含 ^/_ 的占位符，防止被上下标正则误伤。"""
    protected: list[str] = []

    def _sub(m: re.Match) -> str:
        token = f"\x00U{len(protected)}\x00"
        protected.append(m.group(0))
        return token

    text = _URL_RE.sub(_sub, text)
    text = _DOI_RE.sub(_sub, text)
    return text, protected


def _restore(text: str, protected: list[str]) -> str:
    for i, orig in enumerate(protected):
        text = text.replace(f"\x00U{i}\x00", orig)
    return text


def _convert_text(text: str) -> str:
    """对单个文本节点做上下标转换（先保护 URL/DOI，转换完还原）。"""
    text, protected = _protect(text)
    text = _SUP_PAREN.sub(lambda m: f'<sup style="{SUP_STYLE}">{m.group(1)}</sup>', text)
    text = _SUP_NUM.sub(lambda m: f'<sup style="{SUP_STYLE}">{m.group(1)}</sup>', text)
    text = _SUP_WORD.sub(lambda m: f'<sup style="{SUP_STYLE}">{m.group(1)}</sup>', text)
    text = _SUB_BRACE.sub(lambda m: f'<sub style="{SUB_STYLE}">{m.group(1)}</sub>', text)
    text = _SUB_CHAR.sub(lambda m: f'<sub style="{SUB_STYLE}">{m.group(1)}</sub>', text)
    return _restore(text, protected)


class _TextRewriter(HTMLParser):
    """只重写文本节点的上标/下标；跳过 <a> 内容（避免动链接文本）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self._in_a = 0

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._in_a += 1
        self.out.append(self.get_starttag_text() or f"<{tag}>")

    def handle_startendtag(self, tag, attrs):
        self.out.append(self.get_starttag_text() or f"<{tag}/>")

    def handle_endtag(self, tag):
        if tag == "a":
            self._in_a = max(0, self._in_a - 1)
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.out.append(data if self._in_a else _convert_text(data))

    def handle_entityref(self, name):
        self.out.append(f"&{name};")

    def handle_charref(self, name):
        self.out.append(f"&#{name};")

    def handle_comment(self, data):
        self.out.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        self.out.append(f"<!{decl}>")

    def handle_pi(self, data):
        self.out.append(f"<?{data}>")


def polish_html(html: str) -> str:
    """把 html 文本节点中的 ^/_ 上下标记号转成 <sup>/<sub>，返回新 html。"""
    parser = _TextRewriter()
    parser.feed(html)
    parser.close()
    return "".join(parser.out)


# 卡片页脚链接 p：blockquote 内、以 `PubPeer 讨论：` 开头的 <p>（其后可选 <br>DOI：…）
_FOOTER_P_RE = re.compile(r'(<p\b[^>]*\bstyle=")([^"]*)(">PubPeer 讨论：)')
# 文档基准字号（section.container 的内联 style，通常 16px）
_BASE_PX_RE = re.compile(r"font-size:\s*(\d+(?:\.\d+)?)px")


def style_footer_links(html: str) -> str:
    """给卡片页脚「PubPeer 讨论 + DOI」blockquote 的 <p> 注入与英文标题一致的
    小字紧排（font-size calc(Npx*0.85)、line-height 1.3）。幂等：先移除已有
    font-size/line-height 再加新值，重复运行结果不变。"""
    base = _BASE_PX_RE.search(html)
    size = f"calc({base.group(1) if base else '16'}px * 0.85)"

    def _repl(m: re.Match) -> str:
        style = re.sub(r"font-size:[^;\"]*;?", "", m.group(2))
        style = re.sub(r"line-height:[^;\"]*;?", "", style)
        style = " ".join(style.split())  # 移除属性后的残留空档，规范化空白
        new = f"font-size: {size}; line-height: 1.3;"
        if style:
            new += " " + style
        return f"{m.group(1)}{new}{m.group(3)}"

    return _FOOTER_P_RE.sub(_repl, html)


def main() -> None:
    ap = argparse.ArgumentParser(description="周报 HTML 后处理：上下标 → sup/sub + 页脚链接小字紧排（确定性）")
    ap.add_argument("input", type=Path, help="输入 html")
    ap.add_argument("--out", type=Path, default=None, help="输出路径（默认原地写回）")
    args = ap.parse_args()

    html = args.input.read_text(encoding="utf-8")
    new_html = style_footer_links(polish_html(html))
    out_path = args.out or args.input
    out_path.write_text(new_html, encoding="utf-8")
    print(f"polish_html: 已写出 {out_path}（字符变化 {len(new_html) - len(html)}）")


if __name__ == "__main__":
    main()
