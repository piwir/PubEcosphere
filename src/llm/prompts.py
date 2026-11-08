"""提示词加载与校验：周报两份提示词（阶段A 文本提取 + 阶段B 写稿）。

八节骨架来自 `core.prompts.SECTIONS`（与推文流水线共用同一套约定，勿在此重定义）。
提示词文件默认在 `docs/prompts/`（P2 起迁到 `modules/weekly/prompts/`），
可用 `PUBECOSPHERE_LLM_PROMPT_DIR` 或显式参数覆盖。
"""
from __future__ import annotations

from pathlib import Path

from core.prompts import SECTIONS, check_prompts, missing_sections  # noqa: F401 —— 向后兼容再导出

from .config import PROMPT_DIR_DEFAULT

PROMPT_FILES = {
    "extract_text": "prompt_extract_text.md",  # 阶段A 文本提取
    "writer": "prompt_weekly_writer.md",       # 阶段B 周报写稿
}


def load_prompt(name: str, prompt_dir: str | Path | None = None) -> str:
    """加载指定提示词全文。name ∈ {extract_text, writer}。"""
    if name not in PROMPT_FILES:
        raise KeyError(f"unknown prompt name {name!r}, choose from {sorted(PROMPT_FILES)}")
    path = Path(prompt_dir or PROMPT_DIR_DEFAULT) / PROMPT_FILES[name]
    return path.read_text(encoding="utf-8")


def validate_prompt_schema(prompt_dir: str | Path | None = None) -> list[str]:
    """校验两个提示词文件存在且八节齐全。返回问题列表，[] 表示通过。"""
    return check_prompts(prompt_dir or PROMPT_DIR_DEFAULT, PROMPT_FILES)
