"""md2html 产出的周报 HTML 后处理（确定性、幂等，不依赖 LLM）：

1. 数学上下标：把 LaTeX 残记 `^()`/`_x` 转成 <sup>/<sub>（微信不认 KaTeX）。
   `Foxp3^(DTR-GFP/y)` → `Foxp3<sup>DTR-GFP/y</sup>`；`Sum(w_1,...,w_n)` → `w<sub>1</sub>`。
2. 卡片页脚链接：每张卡片末尾「PubPeer 讨论 + DOI」两行 blockquote 的 <p> 注入
   与英文标题一致的 小字紧排（`font-size: calc(Npx * 0.85)`、`line-height: 1.3`）。
3. GitHub 链接修复：简介/结语固定块用 `[..](..)` 语法，md2html 在 blockquote 里会把它
   剥成纯文本 → 包回 <a href> 保持可点击（href 保留完整 URL，显示不带 `https://`，
   与周报其他链接「删去 scheme」一致；幂等）。

页脚链接为什么走后处理而非 CSS：blockquote 位于 `**现状**` 段落之后（结构是
`<p><strong>现状</strong>…</p><blockquote>…`），没有 `h3 + blockquote` 那样的相邻
选择器可命中，juice 又不支持 `:has()`；且微信只保留内联 style → 直接给该 <p> 注入
内联样式（幂等：先清掉已有 font-size/line-height 再加新值）。

用法：python -m llm.polish_html <input.html> [--out <output.html>]
默认原地写回。
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
# 周报链接一律删去 https://（只保留 scheme 之后部分）→ 保护无 scheme 的裸域名 URL，
# 避免 DOI 里的 `_` 被误转成 <sub>（如 doi.org/10.1000/abc_def）
_BARE_URL_RE = re.compile(r"(?:doi\.org|pubpeer\.com|github\.com)/[^\s\"'<>]+")

# 上标：带括号（去括号）→ 数字 → 字母/词
_SUP_PAREN = re.compile(r"\^\(([^()]*)\)")
_SUP_NUM = re.compile(r"\^([-+]?\d+(?:\.\d+)?)")
_SUP_WORD = re.compile(r"\^([A-Za-z][A-Za-z0-9]*)")
# 下标：花括号 → 单字符（保守，避免散文里 _ 分隔误伤）
_SUB_BRACE = re.compile(r"_\{([^{}]*)\}")
_SUB_CHAR = re.compile(r"_([A-Za-z0-9])")


def _protect(text: str) -> tuple[str, list[str]]:
    """把 URL/DOI 换成不含 ^/_ 的占位符，防止被上下标正则误伤。

    正则按「覆盖范围从大到小」执行，避免后一个正则吞掉前一个正则留下的
    占位符造成嵌套（如 `_DOI_RE` 先吃掉 `10.1000/...`，`_BARE_URL_RE` 再把
    `doi.org/<token>` 包一层 → restore 时 token 残留）。"""
    protected: list[str] = []

    def _sub(m: re.Match) -> str:
        token = f"\x00U{len(protected)}\x00"
        protected.append(m.group(0))
        return token

    text = _URL_RE.sub(_sub, text)
    text = _BARE_URL_RE.sub(_sub, text)
    text = _DOI_RE.sub(_sub, text)
    return text, protected


def _restore(text: str, protected: list[str]) -> str:
    for i, orig in enumerate(protected):
        text = text.replace(f"\x00U{i}\x00", orig)
    # 兜底：若仍有残留 token（理论上不该有），循环直到还原干净，避免占位符泄漏进成品
    while "\x00" in text:
        before = text
        for i, orig in enumerate(protected):
            text = text.replace(f"\x00U{i}\x00", orig)
        if text == before:
            break
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
    """只重写文本节点的上标/下标；跳过 <a> 内容（避免动链接文本），
    也跳过 <style>/<script>/<pre>/<code> 的原始文本（CSS/代码里的 `_`/`^` 不能被误转）。"""

    _RAW_TAGS = frozenset({"style", "script", "pre", "code"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self._in_a = 0
        self._in_raw = 0

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._in_a += 1
        if tag in self._RAW_TAGS:
            self._in_raw += 1
        self.out.append(self.get_starttag_text() or f"<{tag}>")

    def handle_startendtag(self, tag, attrs):
        self.out.append(self.get_starttag_text() or f"<{tag}/>")

    def handle_endtag(self, tag):
        if tag == "a":
            self._in_a = max(0, self._in_a - 1)
        if tag in self._RAW_TAGS:
            self._in_raw = max(0, self._in_raw - 1)
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.out.append(data if (self._in_a or self._in_raw) else _convert_text(data))

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


# 简介/结语固定块 GitHub 链接：md2html 在 blockquote 里会剥成纯文本，包回 <a href>。
# 显示文本删去 https://（与周报其他链接一致），href 保留完整 URL 保证可点击。
_GITHUB_URL = "https://github.com/piwir/PubEcosphere"
_GITHUB_DISPLAY = "github.com/piwir/PubEcosphere"
_GITHUB_PLAIN_NEW = f"GitHub：{_GITHUB_DISPLAY}"
_GITHUB_PLAIN_OLD = f"GitHub：{_GITHUB_URL}"
_GITHUB_A = f'GitHub：<a href="{_GITHUB_URL}">{_GITHUB_DISPLAY}</a>'


def repair_github_links(html: str) -> str:
    """把被剥成纯文本的固定 GitHub 链接恢复为 <a href>（可点击，显示不带 https://）。幂等。

    兼容新旧两种写法（无 scheme / 带 scheme 的纯文本），统一归一成 `_GITHUB_A`。"""
    html = html.replace(_GITHUB_PLAIN_OLD, _GITHUB_A)
    return html.replace(_GITHUB_PLAIN_NEW, _GITHUB_A)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="周报 HTML 后处理：上下标 sup/sub + 页脚小字 + GitHub 链接修复（确定性）")
    ap.add_argument("input", type=Path, help="输入 html")
    ap.add_argument("--out", type=Path, default=None, help="输出路径（默认原地写回）")
    args = ap.parse_args()

    html = args.input.read_text(encoding="utf-8")
    new_html = repair_github_links(style_footer_links(polish_html(html)))
    out_path = args.out or args.input
    out_path.write_text(new_html, encoding="utf-8")
    print(f"polish_html: 已写出 {out_path}（字符变化 {len(new_html) - len(html)}）")


if __name__ == "__main__":
    main()
