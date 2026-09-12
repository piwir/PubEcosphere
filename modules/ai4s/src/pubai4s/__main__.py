"""CLI：python -m pubai4s run|fetch|archmap|render|check <args>。

依赖父仓库 PubEcosphere 的 llm.*（可编辑安装）；缺父仓库时干净退出 1。

子命令：
  run     <repo_url>  全流程：抓取 → 提取材料 → 写稿 → 渲染（调 LLM）
  fetch   <repo_url>  只抓取仓库/官网并下载图片（不调 LLM，供人工/subagent 中间处理）
  archmap <out_dir>   从 <out_dir>/inputs/codegraph.txt 生成 arch.png 架构思维导图
  render  <out_dir>   把 <out_dir>/post.md 渲染成 html + base64 html
  check               校验提示词八节齐全
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    import llm  # noqa: F401  父仓库 editable install 提供
except ImportError:
    print("缺少父仓库 PubEcosphere：请先在父仓库根目录 `pip install -e .` 后重试。",
          file=sys.stderr)
    raise SystemExit(1)

from .archmap import ARCH_FILENAME, generate_arch_png, prepend_arch_manifest
from .extract import (README_LIMIT, SUMMARY_LIMIT, ExtractionError,
                      append_image_manifest, extract_material, fetch_stage)
from .generate import PostGenerationError, generate_post
from .github import GitHubClient, RateLimitedError, RepoNotFoundError
from .prompts import SUBMODULE_ROOT, validate_prompts
from .render import render_post

# 子模块本地不落产物：固定到父仓库 output/（SUBMODULE_ROOT = <父仓库>/submodules/PubAI4S）
DEFAULT_OUT_DIR = str(SUBMODULE_ROOT.parent.parent / "output" / "PubAI4S")


def parse_repo_url(url: str) -> tuple[str, str]:
    """https://github.com/o/r、git@github.com:o/r.git、ssh://…/o/r、o/r → (owner, name)。"""
    s = url.strip().rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    if s.startswith("git@"):
        s = s.split(":", 1)[1]
    elif s.startswith("ssh://"):
        s = s.split("github.com/", 1)[1] if "github.com/" in s else s
    elif s.startswith(("https://", "http://")):
        parts = [p for p in s.split("/") if p]
        s = "/".join(parts[-2:])
    parts = [p for p in s.split("/") if p]
    if len(parts) != 2:
        raise ValueError(
            f"无法解析仓库地址：{url!r}（支持 https://github.com/o/r、git@github.com:o/r.git 或 o/r）")
    return parts[0], parts[1]


def _client() -> GitHubClient:
    return GitHubClient(token=os.environ.get("GITHUB_TOKEN", ""))


def _preview(fetched) -> None:
    print(f"仓库：{fetched.info.owner}/{fetched.info.name}  {fetched.info.html_url}")
    print(f"简介：{fetched.info.description or '（无）'}")
    print(f"topics：{', '.join(fetched.info.topics) or '（无）'}")
    print(f"stars：{fetched.info.stars}  forks：{fetched.info.forks}  license：{fetched.info.license}")
    print(f"官网：{fetched.info.homepage or '（未配置）'}")
    print(f"README：{len(fetched.readme)} 字符（超限截断至 {README_LIMIT}）")
    if fetched.website_text:
        print(f"官网抓取：{fetched.crawl_pages} 页 / {fetched.crawl_links} 条链接（结构化文本 {len(fetched.website_text)} 字符）")
    else:
        print("官网：（无或抓取失败）")
    if fetched.codegraph:
        print(f"代码结构：{len(fetched.codegraph)} 字符（codegraph，上限 {SUMMARY_LIMIT}）")
    else:
        print("代码结构：（无，未安装 codegraph 或已降级；--no-codegraph 可跳过）")
    print(f"图片：{len(fetched.images)} 张 → {', '.join(i.filename for i in fetched.images) or '（无）'}")


def cmd_run(args: argparse.Namespace) -> int:
    try:
        owner, name = parse_repo_url(args.repo_url)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir) / f"{owner}-{name}"
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        fetched = fetch_stage(_client(), owner, name, out_dir,
                              use_codegraph=not args.no_codegraph)
    except (RepoNotFoundError, RateLimitedError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        _preview(fetched)
        print(f"输出目录：{out_dir}（--dry-run 只抓取，不调 LLM）")
        return 0

    try:
        material = extract_material(fetched.info, fetched.readme, fetched.website_text,
                                    fetched.codegraph, model=args.model)
    except ExtractionError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    material = append_image_manifest(material, fetched.images)
    (out_dir / "material.md").write_text(material, encoding="utf-8")
    print(f"材料已生成：{out_dir}/material.md（{len(material)} 字符）")

    try:
        post_text = generate_post(material, model=args.model)
    except PostGenerationError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    (out_dir / "post.md").write_text(post_text + "\n", encoding="utf-8")
    print(f"文案已生成：{out_dir}/post.md")

    try:
        render_post(out_dir / "post.md", out_dir)
    except RuntimeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    print(f"发布成品：{out_dir}/")
    print(f"  post.md          文案原文（可手工编辑）")
    print(f"  post.html        md2html 转换")
    print(f"  post-base64.html 图片 base64 内联（浏览器打开 → Ctrl+A → Ctrl+C → 粘贴发布）")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    try:
        owner, name = parse_repo_url(args.repo_url)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir) / f"{owner}-{name}"
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        fetched = fetch_stage(_client(), owner, name, out_dir,
                              use_codegraph=not args.no_codegraph)
    except (RepoNotFoundError, RateLimitedError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    _preview(fetched)
    print(f"输入已落盘：{out_dir}/inputs/（meta.txt / readme.md / website.txt / codegraph.txt 可选）+ images.md + 图片")
    print(f"下一步：把 {out_dir}/inputs/ 与图片交给阶段A 提取（LLM 或 subagent），产物写 {out_dir}/material.md")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    md_path = out_dir / "post.md"
    if not md_path.exists():
        print(f"错误：{md_path} 不存在（先完成写稿步骤）", file=sys.stderr)
        return 1
    try:
        render_post(md_path, out_dir)
    except RuntimeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    print(f"渲染完成：{out_dir}/post.html + post-base64.html")
    return 0


def cmd_archmap(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    cg = out_dir / "inputs" / "codegraph.txt"
    if not cg.exists():
        print(f"错误：{cg} 不存在（无 codegraph 摘要，无法生成架构图）", file=sys.stderr)
        return 1
    project = out_dir.name
    meta = out_dir / "inputs" / "meta.txt"
    if meta.exists():
        first = meta.read_text(encoding="utf-8").splitlines()[0]
        if first.startswith("名称："):
            project = first[len("名称："):].strip()
    target = Path(args.out) if args.out else out_dir / ARCH_FILENAME
    try:
        result = generate_arch_png(cg.read_text(encoding="utf-8"), target, project)
    except Exception as exc:
        print(f"错误：架构图生成失败（{exc}）", file=sys.stderr)
        return 1
    if result is None:
        return 1
    if not args.out:
        prepend_arch_manifest(out_dir / "images.md")
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    problems = validate_prompts()
    if problems:
        for p in problems:
            print(f"  [缺] {p}", file=sys.stderr)
        return 1
    print("提示词校验通过（extract / writer 两段式，八节齐全）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m pubai4s",
        description="把 GitHub AI4S 项目生成可发布的结构化介绍长文（两段式：提取材料 → 写稿 → 渲染）")
    sub = ap.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="全流程（调 LLM）")
    p_run.add_argument("repo_url")
    p_run.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    p_run.add_argument("--model", default=None,
                       help="覆盖 LLM 模型（默认取父仓库 .env 的 PUBECOSPHERE_LLM_MODEL）")
    p_run.add_argument("--dry-run", action="store_true", help="只抓取预览，不调 LLM")
    p_run.add_argument("--no-codegraph", action="store_true",
                       help="跳过 codegraph 代码结构摘要（默认自动）")
    p_run.set_defaults(func=cmd_run)

    p_fetch = sub.add_parser("fetch", help="只抓取仓库/官网 + 下载图片（不调 LLM）")
    p_fetch.add_argument("repo_url")
    p_fetch.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    p_fetch.add_argument("--no-codegraph", action="store_true",
                         help="跳过 codegraph 代码结构摘要（默认自动）")
    p_fetch.set_defaults(func=cmd_fetch)

    p_render = sub.add_parser("render", help="把 <dir>/post.md 渲染成 html + base64 html")
    p_render.add_argument("out_dir")
    p_render.set_defaults(func=cmd_render)

    p_arch = sub.add_parser("archmap",
                            help="从 <out_dir>/inputs/codegraph.txt 生成 arch.png 架构思维导图")
    p_arch.add_argument("out_dir")
    p_arch.add_argument("--out", default=None,
                        help="覆盖输出 PNG 路径（默认 <out_dir>/arch.png，并同步 images.md 清单首位）")
    p_arch.set_defaults(func=cmd_archmap)

    sub.add_parser("check", help="校验提示词八节齐全").set_defaults(func=cmd_check)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
