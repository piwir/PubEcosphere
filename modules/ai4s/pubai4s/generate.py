"""阶段B：材料 md → 发布文案（LLM 写稿）。

输入是阶段A 产物 material.md（含末尾「已下载图片清单」小节），
输出为可直接发布的短帖 markdown 正文（写入 post.md，再由 render 转 html）。
"""
from __future__ import annotations

from llm.client import LLMClient, LLMError
from llm.config import LLMConfig

from .prompts import load_prompt


class PostGenerationError(RuntimeError):
    pass


def generate_post(material: str, model: str | None = None,
                  prompt_dir: str | None = None) -> str:
    """材料 md → 发布文案正文。空输出 → PostGenerationError。"""
    system = load_prompt("writer", prompt_dir)
    client = LLMClient(LLMConfig.from_env(prompt_dir=prompt_dir))
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": material},
    ]
    try:
        text = client.chat(messages, model=model, temperature=0.7, max_tokens=None)
    except LLMError as exc:
        raise PostGenerationError(f"LLM 调用失败：{exc}") from exc
    text = text.strip()
    if not text:
        raise PostGenerationError("模型返回空输出，未生成文案")
    return text
