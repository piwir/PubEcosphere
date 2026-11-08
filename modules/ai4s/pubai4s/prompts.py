"""提示词加载与校验：推文两份提示词（阶段A 信息提取 + 阶段B 写稿）。

提示词随本模块自持：`modules/ai4s/prompts/`。八节骨架来自 `core.prompts.SECTIONS`
（与周报流水线同一套约定，不再反向 import 周报的包）。
目录覆盖优先级：显式参数 > `PUBECOSPHERE_AI4S_PROMPT_DIR` 环境变量 > 本模块默认目录。
"""
from __future__ import annotations

import os
from pathlib import Path

from core.prompts import SECTIONS, check_prompts, missing_sections  # noqa: F401 —— 再导出

# modules/ai4s/pubai4s/prompts.py → modules/ai4s/prompts
PROMPT_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "prompts"

PROMPT_FILES = {
    "extract": "prompt_extract.md",
    "writer": "prompt_writer.md",
}


def prompt_dir(override: str | Path | None = None) -> Path:
    """解析提示词目录：显式参数 > 环境变量 > 模块内默认目录。"""
    if override:
        return Path(override)
    env = os.environ.get("PUBECOSPHERE_AI4S_PROMPT_DIR")
    return Path(env) if env else PROMPT_DIR_DEFAULT


def load_prompt(name: str, directory: str | Path | None = None) -> str:
    """加载指定提示词全文。name ∈ {extract, writer}。"""
    if name not in PROMPT_FILES:
        raise KeyError(f"unknown prompt name {name!r}, choose from {sorted(PROMPT_FILES)}")
    return (prompt_dir(directory) / PROMPT_FILES[name]).read_text(encoding="utf-8")


def validate_prompts(directory: str | Path | None = None) -> list[str]:
    """校验两个提示词文件存在且八节齐全。返回问题列表，[] 表示通过。"""
    return check_prompts(prompt_dir(directory), PROMPT_FILES)
