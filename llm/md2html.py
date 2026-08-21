"""md → 微信兼容 HTML 转换（仓库内 vendor，无外部 skill 依赖）。

转换器为仓库内 vendored（`vendor/md2html/` 包 + `vendor/md2html-cli/scripts/render.ts` 薄 CLI，
经裁剪：去掉 mermaid，手调 CSS 随 vendor 双份带入库），不依赖任何外部 skill；
手调 CSS 是仓库资产，换机/重装不丢。

bun 运行时默认经 `npx -y bun` 启动（用户已确认默认走 npx）；可用环境变量 BUN
指向本地 bun 可执行文件覆盖。转换器输出固定为输入 md 同目录 `.html`
（md → html，与 baoyu-md 原行为一致），不提供 --out。

用法：
    python -m llm md2html <input.md> [--keep-title] [--theme default]
    python -m llm md2html --check       # 检查 bun / render.ts / node_modules 就绪，退出 0/1
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RENDER_TS = REPO_ROOT / "vendor" / "md2html-cli" / "scripts" / "render.ts"
NODE_MODULES = REPO_ROOT / "vendor" / "md2html-cli" / "node_modules"


def resolve_bun() -> list[str]:
    """bun 运行命令。BUN 环境变量可指向本地 bun；否则默认 `npx -y bun`（用户定案）。"""
    custom = os.environ.get("BUN")
    if custom:
        return [custom]
    return ["npx", "-y", "bun"]


def resolve_main() -> Path:
    """转换器薄 CLI 入口。环境变量 MD2HTML_MAIN 可覆盖（默认 vendored render.ts）。"""
    override = os.environ.get("MD2HTML_MAIN")
    target = Path(override) if override else RENDER_TS
    if not target.exists():
        raise FileNotFoundError(
            f"未找到转换器入口：{target}。仓库内请先 `cd vendor/md2html-cli && npx -y bun install`")
    return target


def _bun_available() -> bool:
    """bun/npx 是否可执行。BUN 显式指定时检查该文件；否则看 PATH 上有没有 npx/bun。"""
    custom = os.environ.get("BUN")
    if custom:
        return Path(custom).exists() and os.access(custom, os.X_OK)
    return shutil.which("npx") is not None or shutil.which("bun") is not None


def check_ready() -> tuple[bool, list[str]]:
    """返回 (是否就绪, 问题列表)。--check 与 preflight 用。"""
    problems: list[str] = []
    if not _bun_available():
        problems.append(
            "找不到 npx/bun（默认经 `npx -y bun` 启动）。请安装 Node.js（含 npx）或 bun，或用 BUN 指向本地 bun")
    if not RENDER_TS.exists():
        problems.append(f"缺少转换器脚本 {RENDER_TS}")
    if not (NODE_MODULES / "md2html").exists():
        problems.append(
            f"缺少依赖（node_modules）。请 `cd vendor/md2html-cli && npx -y bun install`")
    return (not problems, problems)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m llm md2html", description=__doc__)
    ap.add_argument("input", nargs="?", type=Path, help="输入 md（输出到同目录 .html）")
    ap.add_argument("--keep-title", action="store_true",
                    help="保留正文首个标题（主标题 `# PubPeer 周报`）")
    ap.add_argument("--theme", default="default", help="主题名（默认 default）")
    ap.add_argument("--check", action="store_true", help="检查 bun / 脚本 / 依赖就绪")
    args = ap.parse_args(argv)

    if args.check:
        ok, problems = check_ready()
        bun_cmd = resolve_bun()
        print(f"bun 运行命令：{' '.join(bun_cmd)}")
        print(f"转换器脚本：  {RENDER_TS}")
        if ok:
            print("md2html: 就绪（bun + render.ts + node_modules）")
            return 0
        for p in problems:
            print(f"  [缺] {p}", file=sys.stderr)
        return 1

    if not args.input:
        print("请提供输入 md 路径，或 --check。", file=sys.stderr)
        return 1
    md = args.input
    if not md.exists():
        print(f"输入不存在：{md}", file=sys.stderr)
        return 1

    ok, problems = check_ready()
    if not ok:
        for p in problems:
            print(p, file=sys.stderr)
        return 1

    main_ts = resolve_main()
    cmd = [*resolve_bun(), str(main_ts), str(md),
           "--theme", args.theme]
    if args.keep_title:
        cmd.append("--keep-title")

    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        # 失败时把转换器 stdout 透传便于诊断
        sys.stdout.write(proc.stdout)
        return proc.returncode
    # 成功时不把转换器 stdout（JSON，含 htmlPath/localPath 等绝对路径）透传，
    # 避免 MCP 返回值夹带本机路径；只回一行干净的确认。
    print(f"md2html: {md} → {md.with_suffix('.html')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
