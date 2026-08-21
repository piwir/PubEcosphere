"""`python -m llm` 入口：check / selftest / flatten / assemble / generate / md2html。

默认不联网：generate 只有不带 --dry-run 时才真正调 API。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import md2html as md2html_mod
from . import selftest
from .assemble import assemble_weekly
from .client import LLMClient
from .config import LLMConfig
from .flatten import flatten_material, print_flatten_summary
from .generate import GenerationBlocked, generate_issue
from .prompts import validate_prompt_schema


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

def cmd_check(args) -> int:
    problems = validate_prompt_schema()
    if problems:
        print("提示词校验失败：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("提示词校验通过：两文件存在，八节齐全。")
    return 0


def cmd_selftest(args) -> int:
    results = selftest.run_selftest()
    failed = 0
    for name, ok, detail in results:
        print(f"{'[PASS]' if ok else '[FAIL]'} {name}: {detail}")
        failed += 0 if ok else 1
    print(f"selftest: {len(results) - failed}/{len(results)} 通过")
    return 1 if failed else 0


def cmd_flatten(args) -> int:
    summary = flatten_material(args.material_dir, args.upload_dir,
                               manifest=args.manifest)
    print_flatten_summary(summary)
    return 0


def cmd_assemble(args) -> int:
    weekly = Path(args.weekly_dir)
    if args.draft:
        draft = Path(args.draft)
    elif args.issue:
        draft = weekly / f"{args.issue}_draft.md"
    else:
        print("--issue 或 --draft 至少提供一个", file=sys.stderr)
        return 1
    if not draft.exists():
        print(f"草稿不存在：{draft}", file=sys.stderr)
        return 1
    warnings = assemble_weekly(draft, args.material_dir, weekly,
                               issue=args.issue, dry_run=args.dry_run)
    if args.dry_run:
        print("[dry-run] 不写盘。以下为将产生的告警：")
    for w in warnings:
        print(f"  [告警] {w}")
    if not warnings:
        print("无告警，全部图片引用合法。")
    return 0


def cmd_generate(args) -> int:
    """单模型端到端：提取 + 写稿（同一模型）；--dry-run 只打印消息，不联网。"""
    config = LLMConfig.from_env()
    if not config.api_key and not args.dry_run:
        print("未设置 PUBECOSPHERE_LLM_API_KEY，无法调用 API（可加 --dry-run 只看消息）",
              file=sys.stderr)
        return 1
    client = LLMClient(config) if (config.api_key and not args.dry_run) else None
    try:
        warnings = generate_issue(
            args.material_dir, args.weekly_dir, issue=args.issue,
            client=client, config=config, model=args.model,
            limit=args.limit, pid=args.pid, manifest=args.manifest,
            assemble=args.assemble, dry_run=args.dry_run)
    except GenerationBlocked as exc:
        print(f"generate 阻塞性失败：{exc}（无材料/空提取/空草稿），退出 1。", file=sys.stderr)
        return 1
    for w in warnings:
        print(f"  [告警] {w}")
    if args.dry_run:
        print("[dry-run] 未调用 API、未写盘。")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m llm", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="校验 docs/prompts 两文件与八节")
    sub.add_parser("selftest", help="离线自测")

    p = sub.add_parser("md2html", help="md → 微信兼容 HTML（仓库内 vendored 转换器）")
    p.add_argument("input", nargs="?", type=Path, default=None)
    p.add_argument("--keep-title", action="store_true")
    p.add_argument("--theme", default="default")
    p.add_argument("--check", action="store_true", help="检查 bun/脚本/依赖就绪")

    p = sub.add_parser("flatten", help="摊平 material → upload 扁平素材文件夹")
    p.add_argument("--material-dir", required=True, type=Path)
    p.add_argument("--upload-dir", required=True, type=Path)
    p.add_argument("--manifest", type=Path, default=None)

    p = sub.add_parser("assemble", help="排版：草稿 → 成品 md + 图复制（离线）")
    p.add_argument("--material-dir", required=True, type=Path)
    p.add_argument("--weekly-dir", required=True, type=Path)
    p.add_argument("--issue", default="")
    p.add_argument("--draft", type=Path, default=None)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("generate", help="单模型端到端：提取 + 写稿（同一模型，--dry-run 只打印消息）")
    p.add_argument("--material-dir", required=True, type=Path)
    p.add_argument("--weekly-dir", required=True, type=Path)
    p.add_argument("--issue", default="")
    p.add_argument("--model", default=None, help="覆盖默认单模型（PUBECOSPHERE_LLM_MODEL）")
    p.add_argument("--manifest", type=Path, default=None)
    p.add_argument("--limit", type=int, default=None, help="只处理前 N 篇")
    p.add_argument("--pid", default=None, help="只处理指定 pid 一篇")
    p.add_argument("--assemble", action="store_true", help="写稿后自动排版成品 md + 复制图片")
    p.add_argument("--dry-run", action="store_true", help="只打印将发送的消息，不联网、不写盘")

    args = parser.parse_args(argv)
    handlers = {
        "check": cmd_check,
        "selftest": cmd_selftest,
        "md2html": cmd_md2html,
        "flatten": cmd_flatten,
        "assemble": cmd_assemble,
        "generate": cmd_generate,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
