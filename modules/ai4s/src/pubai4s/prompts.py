"""提示词加载：指向子模块仓库根的 docs/prompts/（勿用父仓库 repopath）。

两段式与父仓库同骨架：extract（阶段A 信息提取）+ writer（阶段B 写稿）。
"""
from __future__ import annotations

from pathlib import Path

# submodules/PubAI4S/src/pubai4s/prompts.py → 子模块仓库根（比父仓库 repopath 深一级）
SUBMODULE_ROOT = Path(__file__).resolve().parent.parent.parent

PROMPT_FILES = {
    "extract": "prompt_extract.md",
    "writer": "prompt_writer.md",
}


def load_prompt(name: str, prompt_dir: str | Path | None = None) -> str:
    """加载指定提示词全文。name ∈ {extract, writer}。"""
    if name not in PROMPT_FILES:
        raise KeyError(f"unknown prompt name {name!r}, choose from {sorted(PROMPT_FILES)}")
    path = Path(prompt_dir or SUBMODULE_ROOT / "docs" / "prompts") / PROMPT_FILES[name]
    return path.read_text(encoding="utf-8")


def validate_prompts(prompt_dir: str | Path | None = None) -> list[str]:
    """校验两个提示词文件存在且八节齐全（复用父仓库 llm.prompts.SECTIONS）。"""
    from llm.prompts import SECTIONS  # 父仓库 editable install 提供

    problems: list[str] = []
    for name in PROMPT_FILES:
        path = Path(prompt_dir or SUBMODULE_ROOT / "docs" / "prompts") / PROMPT_FILES[name]
        if not path.exists():
            problems.append(f"{PROMPT_FILES[name]}（文件不存在：{path}）")
            continue
        text = path.read_text(encoding="utf-8")
        absent = [s for s in SECTIONS if s not in text]
        if absent:
            problems.append(f"{PROMPT_FILES[name]}（缺少节：{'、'.join(absent)}）")
    return problems
