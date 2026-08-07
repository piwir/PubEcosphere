"""LLM 接入配置：OpenAI 兼容 `chat/completions` 的参数与环境变量。

预留未启用，见 docs/llm-scheme.md §8。默认 base_url 指向 DeepSeek 的
OpenAI 兼容端点；换任何 OpenAI 兼容服务只需改 base_url / api_key / 模型名。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
PROMPT_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "docs" / "prompts"


@dataclass
class LLMConfig:
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    vision_model: str = "deepseek-chat"
    writer_model: str = "deepseek-chat"
    timeout: float = 120.0
    max_retries: int = 3
    backoff: float = 5.0
    prompt_dir: str = str(PROMPT_DIR_DEFAULT)

    @classmethod
    def from_env(cls, prompt_dir: str | None = None) -> "LLMConfig":
        return cls(
            base_url=os.environ.get("PUBECOSPHERE_LLM_BASE_URL", DEFAULT_BASE_URL),
            api_key=os.environ.get("PUBECOSPHERE_LLM_API_KEY", ""),
            vision_model=os.environ.get("PUBECOSPHERE_LLM_VISION_MODEL", "deepseek-chat"),
            writer_model=os.environ.get("PUBECOSPHERE_LLM_WRITER_MODEL", "deepseek-chat"),
            timeout=float(os.environ.get("PUBECOSPHERE_LLM_TIMEOUT", "120")),
            max_retries=int(os.environ.get("PUBECOSPHERE_LLM_MAX_RETRIES", "3")),
            backoff=float(os.environ.get("PUBECOSPHERE_LLM_BACKOFF", "5")),
            prompt_dir=prompt_dir
            or os.environ.get("PUBECOSPHERE_LLM_PROMPT_DIR", str(PROMPT_DIR_DEFAULT)),
        )
