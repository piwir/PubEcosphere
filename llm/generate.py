"""单模型端到端路径：提取 + 写稿用**同一个模型**（无多模态，DeepSeek 文本即可）。

与多模态双模型路径（vision + writer）平行，纯文本：
- 阶段A：逐篇（或一批）素材 md → 文本提取提示词（prompt_extract_text）→ 冲突点提取块。
  图片描述全部来自评论者/作者配图时自己写下的原话（素材 md 的「图（推文用）」小节与
  相关评论正文），模型无读图能力、绝不臆测图内看不到的内容。
- 阶段B：归集的 stageA_combined.md → 写稿提示词（prompt_weekly_writer）→ 整期周报草稿。

命令：`python -m llm generate --material-dir … --weekly-dir … --issue -1`（--dry-run 只打印消息）。
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

from .assemble import assemble_weekly
from .client import LLMClient
from .config import LLMConfig
from .prompts import load_prompt
from .vision import load_manifest, prepend_metadata


def material_papers(material_dir: str | Path) -> list[Path]:
    """material 下各篇目录的 <pid>.md（按 pid 排序）。"""
    mat = Path(material_dir)
    return sorted(p / f"{p.name}.md" for p in mat.iterdir() if p.is_dir() and (p / f"{p.name}.md").exists())


def build_extract_messages(papers: Iterable[str | Path], prompt: str,
                           meta: Optional[dict] = None) -> list[dict]:
    """把若干篇素材 md 拼成一个 user 文本（每篇顶部补 分类/IF），system=extract 提示词。

    与多模态 vision 不同：这里**不打包图片**，纯文本——模型依据评论者原话概括图片内容。
    """
    blocks: list[str] = []
    for paper in papers:
        text = Path(paper).read_text(encoding="utf-8")
        info = meta.get(Path(paper).parent.name) if meta else None
        if info:
            text = prepend_metadata(text, info)
        blocks.append(text)
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "\n\n---\n\n".join(blocks)},
    ]


def _join_blocks(blocks: Iterable[str]) -> str:
    """多个提取块归集成 stageA_combined；块间 `---` 分隔，去首尾空行。"""
    return "\n\n---\n\n".join(b.strip() for b in blocks if b and b.strip())


def issue_header(issue: str | int, run_id: str, week_start: str | None = None) -> str:
    """写稿输入顶部头部：`期号：…（run …）`；正整数期号追加 `数据收集：M.DD–M.DD`。

    issue 可能是 CLI 传来的字符串（"--issue 1"）也可能是 int；空/0/负值不注入
    （`--issue -1` 测试期避免展示无意义的过去窗口）。"""
    header = f"期号：{issue or '-'}（run {run_id}）"
    num = int(issue) if str(issue).lstrip("-").isdigit() else None
    if num and num > 0:
        header += f"\n数据收集：{weekly_date_range(num, week_start)}"
    return header


def weekly_date_range(issue: int, week_start: str | None = None) -> str:
    """按 WEEK_START 环境变量（默认 2026-08-03）+ 期号推算本期 7 天数据收集窗口。

    起始 = week_start + (issue-1)*7，结束 = 起始 + 6 天；格式 `M.DD–M.DD`（如 `8.03–8.09`）。
    导语第一句的 `（素材收集 <FROM>–<TO>）` 由写稿模型从该值转抄。
    """
    base = date.fromisoformat(week_start or os.environ.get("WEEK_START", "2026-08-03"))
    start = base + timedelta(days=(issue - 1) * 7)
    end = start + timedelta(days=6)
    return f"{start.month}.{start.day:02d}–{end.month}.{end.day:02d}"


def generate_issue(material_dir: str | Path, weekly_dir: str | Path, issue: str = "",
                   client: Optional[LLMClient] = None, config: Optional[LLMConfig] = None,
                   model: str | None = None, limit: int | None = None,
                   pid: str | None = None, manifest: Optional[str | Path | dict] = None,
                   assemble: bool = False, dry_run: bool = False) -> list[str]:
    """单模型端到端：提取（全部/limit/单篇）→ 归集 → 写稿 →（可选）排版成品。

    返回告警列表。`--dry-run` 只打印将发送的消息片段，不联网、不写盘。
    """
    warnings: list[str] = []
    papers = material_papers(material_dir)
    if pid:
        papers = [p for p in papers if p.parent.name == pid]
        if not papers:
            warnings.append(f"material 里没有 pid={pid} 的目录")
    if limit:
        papers = papers[:limit]
    if not papers:
        warnings.append("material 里没有可处理的论文 md")
        print("generate: 没有可处理的论文，中止。", file=sys.stderr)
        return warnings

    cfg = config or LLMConfig.from_env()
    client = client or LLMClient(cfg)
    run_model = model or cfg.model
    meta = load_manifest(manifest)

    extract_prompt = load_prompt("extract_text", cfg.prompt_dir)
    writer_prompt = load_prompt("writer", cfg.prompt_dir)

    # 阶段A：同一模型、纯文本提取（一批发送）
    extract_msgs = build_extract_messages(papers, extract_prompt, meta=meta)
    if dry_run:
        print("[dry-run] 阶段A 文本提取：单模型", run_model, f"，{len(papers)} 篇")
        print("  user 前 800 字：")
        print(extract_msgs[1]["content"][:800].replace("\n", "\n  "))
        print("[dry-run] 阶段B 写稿将在阶段A 完成后进行；--dry-run 不调 API。")
        return warnings

    print(f"阶段A：文本提取 {len(papers)} 篇 → {run_model} …", flush=True)
    extracted = client.chat(extract_msgs, model=run_model, max_tokens=cfg.max_tokens)
    blocks = _join_blocks([extracted])
    if not blocks:
        warnings.append("阶段A 返回为空，无法写稿")
        print("generate: 阶段A 返回为空，中止。", file=sys.stderr)
        return warnings
    run_id = date.today().isoformat()
    # 头部 `期号：…（run …）` 与写稿提示词契约一致（提示词从输入顶部 `期号：` 行取期号），
    # 同时写进 stageA_combined.md 与写稿 user 消息——修「issue 未知」标题。
    # 正整数期号追加 `数据收集：M.DD–M.DD`（WEEK_START 环境变量 + 期号推算），供导语第一句转抄；
    # `--issue -1` 测试期不注入，避免展示无意义的过去窗口（issue 可能是 str 或 int，见 issue_header）。
    header = issue_header(issue, run_id)
    combined = f"{header}\n\n{blocks}"

    # 归集写盘（供人工审核阶段A）
    weekly = Path(weekly_dir)
    # 整体重建：整期全量跑时清空 weekly（限 --limit/--pid 的部分跑不清，避免误删整期产物）；
    # 位置在阶段A 成功后，失败时旧 weekly 保留
    if not (limit or pid):
        shutil.rmtree(weekly, ignore_errors=True)
    weekly.mkdir(parents=True, exist_ok=True)
    combined_path = weekly / "stageA_combined.md"
    combined_path.write_text(combined + "\n", encoding="utf-8")
    print(f"  已写出：{combined_path}")

    # 阶段B：写稿
    print(f"阶段B：整期周报写稿 → {run_model} …", flush=True)
    writer_msgs = [{"role": "system", "content": writer_prompt},
                   {"role": "user", "content": combined}]
    draft = client.chat(writer_msgs, model=run_model, max_tokens=cfg.max_tokens)
    if not draft.strip():
        warnings.append("阶段B 返回空草稿")
        print("generate: 阶段B 返回为空。", file=sys.stderr)
        return warnings
    draft_path = weekly / (f"{issue}_draft.md" if issue else "draft.md")
    draft_path.write_text(draft, encoding="utf-8")
    print(f"  已写出：{draft_path}")

    if assemble:
        asm_warnings = assemble_weekly(draft_path, material_dir, weekly, issue=issue)
        warnings.extend(asm_warnings)
    return warnings
