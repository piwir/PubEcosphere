"""提示词单一来源：从 docs/prompts/ 加载两阶段提示词并校验八节齐全。

八节骨架沿用 docs/prompt_example.txt：角色设定 / 背景 / 任务目标 /
内容结构要求 / 术语定义规范 / 数字与科学计数法格式规范 / 输出格式 / 语气风格。
"""
from __future__ import annotations

from pathlib import Path

from .config import PROMPT_DIR_DEFAULT

PROMPT_FILES = {
    "vision": "prompt_vision_extract.md",   # 阶段A 多模态提取
    "writer": "prompt_weekly_writer.md",    # 阶段B 周报写稿
    "extract_text": "prompt_extract_text.md",  # 阶段A 文本提取
}
SECTIONS = [
    "【角色设定】",
    "【背景】",
    "【任务目标】",
    "【内容结构要求】",
    "【术语定义规范】",
    "【数字与科学计数法格式规范】",
    "【输出格式】",
    "【语气风格】",
]


def load_prompt(name: str, prompt_dir: str | Path | None = None) -> str:
    """加载指定提示词全文。name ∈ {vision, writer}。"""
    if name not in PROMPT_FILES:
        raise KeyError(f"unknown prompt name {name!r}, choose from {sorted(PROMPT_FILES)}")
    path = Path(prompt_dir or PROMPT_DIR_DEFAULT) / PROMPT_FILES[name]
    return path.read_text(encoding="utf-8")


def validate_prompt_schema(prompt_dir: str | Path | None = None) -> list[str]:
    """校验两文件存在且八节齐全。返回问题列表，[] 表示通过。"""
    problems: list[str] = []
    for name in PROMPT_FILES:
        path = Path(prompt_dir or PROMPT_DIR_DEFAULT) / PROMPT_FILES[name]
        if not path.exists():
            problems.append(f"{path.name}（文件不存在）")
            continue
        text = path.read_text(encoding="utf-8")
        absent = [s for s in SECTIONS if s not in text]
        if absent:
            problems.append(f"{path.name}（缺少节：{'、'.join(absent)}）")
    return problems
