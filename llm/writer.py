"""阶段B 周报写稿：把 stageA_combined.md 拼成文本 messages 交给写稿模型。

阶段B 不读图、不臆测图片内容——图片内容全部依赖阶段A 提取块的「可用图片清单」。
"""
from __future__ import annotations

from pathlib import Path

from .client import LLMClient


def build_writer_messages(stage_a_text: str, prompt: str) -> list[dict]:
    """system=阶段B 提示词；user=阶段A 提取产物全文（纯文本）。"""
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": stage_a_text},
    ]


def run_writer(stage_a_path: str | Path, prompt: str, client: LLMClient,
               model: str | None = None) -> str:
    """阶段B 完整调用：读 stageA_combined.md → 写稿模型返回整期周报 md。"""
    stage_a_text = Path(stage_a_path).read_text(encoding="utf-8")
    messages = build_writer_messages(stage_a_text, prompt)
    return client.chat(messages, model=model)
