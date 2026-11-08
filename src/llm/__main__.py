"""`python -m llm` 入口：通用 LLM 与渲染工具（md2html / inline_images）。

周报写稿（generate / assemble / check / selftest）在 `modules/weekly/writer`
（`python -m writer …`）；这里只放两条产品线共用的东西：

    md2html       md → 微信兼容 HTML（仓库内 vendored 转换器）
    inline_images 图片 base64 内联（微信手动粘贴专用）

默认不联网。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from . import md2html as md2html_mod


def cmd_md2html(args) -> int:
    """md → 微信兼容 HTML（仓库内 vendored 转换器，去外部 skill 依赖）。"""
    cli: list[str] = []
    if args.input:
        cli.append(str(args.input))
    if args.keep_title:
        cli.append("--keep-title")
    if args.theme:
        cli += ["--theme", args.theme]
    if args.check:
        cli.append("--check")
    return md2html_mod.main(cli)


def cmd_inline_images(args) -> int:
    """图片 base64 内联（等价 `python -m llm.inline_images <html>`）。"""
    from .inline_images import main as inline_main
    argv = [str(args.html)]
    if args.out:
        argv += ["--out", str(args.out)]
    return inline_main(argv)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m llm", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("md2html", help="md → 微信兼容 HTML（仓库内 vendored 转换器）")
    p.add_argument("input", nargs="?", type=Path, default=None)
    p.add_argument("--keep-title", action="store_true")
    p.add_argument("--theme", default="default")
    p.add_argument("--check", action="store_true", help="检查 bun/脚本/依赖就绪")
    p.set_defaults(func=cmd_md2html)

    p = sub.add_parser("inline_images", help="图片 base64 内联（产 <名>-base64.html）")
    p.add_argument("html", type=Path)
    p.add_argument("--out", type=Path, default=None)
    p.set_defaults(func=cmd_inline_images)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
