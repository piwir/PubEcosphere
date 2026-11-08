"""`python -m writer` 入口：周报写稿与排版（check / selftest / assemble / generate）。

默认不联网：`generate` 不带 `--dry-run` 时才真正调 `.env` 模型。
本机首选流程不用 `generate`（由 agent 按提示词写稿），它只服务无头/定时场景。
md→html 渲染不在这里：`python -m llm md2html`（通用渲染层）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from llm.client import LLMClient
from llm.config import LLMConfig

from . import selftest
from .assemble import assemble_weekly
from .generate import GenerationBlocked, generate_issue
from .prompts import PROMPT_DIR_DEFAULT, validate_prompt_schema


def cmd_check(args) -> int:
    problems = validate_prompt_schema(args.prompt_dir)
    if problems:
        print(f"提示词校验失败（目录 {args.prompt_dir or PROMPT_DIR_DEFAULT}）：")
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
    config = LLMConfig.from_env(prompt_dir=args.prompt_dir)
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
    parser = argparse.ArgumentParser(prog="python -m writer", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="校验 modules/weekly/prompts 两文件与八节")
    p.add_argument("--prompt-dir", default=None, help="覆盖提示词目录（默认本模块 prompts/）")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("selftest", help="离线自测（假 transport，不联网）")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("assemble", help="排版：草稿 → 成品 md + 图复制（离线）")
    p.add_argument("--material-dir", required=True, type=Path)
    p.add_argument("--weekly-dir", required=True, type=Path)
    p.add_argument("--issue", default="")
    p.add_argument("--draft", type=Path, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_assemble)

    p = sub.add_parser("generate", help="单模型端到端：提取 + 写稿（--dry-run 只打印消息）")
    p.add_argument("--material-dir", required=True, type=Path)
    p.add_argument("--weekly-dir", required=True, type=Path)
    p.add_argument("--issue", default="")
    p.add_argument("--model", default=None, help="覆盖默认单模型（PUBECOSPHERE_LLM_MODEL）")
    p.add_argument("--manifest", type=Path, default=None)
    p.add_argument("--limit", type=int, default=None, help="只处理前 N 篇")
    p.add_argument("--pid", default=None, help="只处理指定 pid 一篇")
    p.add_argument("--assemble", action="store_true", help="写稿后自动排版成品 md + 复制图片")
    p.add_argument("--prompt-dir", default=None, help="覆盖提示词目录（默认本模块 prompts/）")
    p.add_argument("--dry-run", action="store_true", help="只打印将发送的消息，不联网、不写盘")
    p.set_defaults(func=cmd_generate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
