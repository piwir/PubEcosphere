"""提示词骨架与校验：周报与推文两条线共用一套八节约定。

两条产品线各有自己的提示词文件（周报 `modules/weekly/prompts/`、推文
`modules/ai4s/prompts/`），但**骨架相同**（沿用 `docs/prompt_example.txt`）：
角色设定 / 背景 / 任务目标 / 内容结构要求 / 术语定义规范 / 数字与科学计数法格式规范 /
输出格式 / 语气风格。此前推文包靠 `from llm.prompts import SECTIONS` 反向依赖，
现在两边都从这里取：

    SECTIONS                       八节标题（有序）
    missing_sections(text)         文本缺少哪几节
    check_prompts(dir, files)      目录下指定提示词文件是否齐全、八节是否完整
"""
from __future__ import annotations

from pathlib import Path

SECTIONS: tuple[str, ...] = (
    "【角色设定】",
    "【背景】",
    "【任务目标】",
    "【内容结构要求】",
    "【术语定义规范】",
    "【数字与科学计数法格式规范】",
    "【输出格式】",
    "【语气风格】",
)


def missing_sections(text: str) -> list[str]:
    """返回 `text` 中缺失的节标题（空列表 = 八节齐全）。"""
    return [s for s in SECTIONS if s not in text]


def check_prompts(prompt_dir: str | Path, files: dict[str, str]) -> list[str]:
    """校验提示词文件齐全且八节完整。

    `files` 形如 `{"extract": "prompt_extract.md", "writer": "prompt_writer.md"}`。
    返回问题列表（空 = 通过）；消息里带人类可读标签（键名）与原因。
    """
    problems: list[str] = []
    base = Path(prompt_dir)
    for label, filename in files.items():
        path = base / filename
        if not path.exists():
            problems.append(f"{label}：{filename}（文件不存在：{path}）")
            continue
        absent = missing_sections(path.read_text(encoding="utf-8"))
        if absent:
            problems.append(f"{label}：{filename}（缺少节：{'、'.join(absent)}）")
    return problems
