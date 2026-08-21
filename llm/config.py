"""LLM 接入配置：OpenAI 兼容 `chat/completions` 的参数与环境变量。

默认 base_url 指向 DeepSeek 的 OpenAI 兼容端点；换任何 OpenAI 兼容服务只需改
base_url / api_key / 模型名。

密钥安全（开源准备）：**密钥绝不硬编码**。读取优先级：进程环境变量 →
仓库根 `.env`（KEY=VALUE 每行一个，`#` 注释；gitignored，提交 `.env.example`
占位模板）。从 .env 读到的值仅注入进程环境，不打印、不进日志、不进源码。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
PROMPT_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "docs" / "prompts"

# 仓库根（llm/ 的父目录）下的 .env；gitignored，不随代码提交。
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

# 单模型路径（extract_text + writer 用同一模型）
ENV_MODEL = "PUBECOSPHERE_LLM_MODEL"
ENV_MAX_TOKENS = "PUBECOSPHERE_LLM_MAX_TOKENS"


def load_dotenv(path: str | Path = ENV_FILE) -> None:
    """把 `.env` 里 KEY=VALUE 加载进 os.environ（已有值不覆盖）。纯 stdlib。"""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()  # 模块导入时一次性加载仓库根 .env（幂等、值只填缺的）


# 默认模型：DeepSeek V4 Flash。
DEFAULT_MODEL = "deepseek-v4-flash"
# 输出硬上限：顶满模型上限（384K），不人为限流；不传的话 API 默认仅 4096，整期写稿必截断。
DEFAULT_MAX_TOKENS = 393216


@dataclass
class LLMConfig:
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    # 单模型路径：extract_text 与 writer 共用。
    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS  # 顶满模型上限，不人为限流
    timeout: float = 120.0
    max_retries: int = 3
    backoff: float = 5.0
    prompt_dir: str = str(PROMPT_DIR_DEFAULT)

    @classmethod
    def from_env(cls, prompt_dir: str | None = None) -> "LLMConfig":
        vals: dict = {
            "base_url": os.environ.get("PUBECOSPHERE_LLM_BASE_URL", DEFAULT_BASE_URL),
            "api_key": os.environ.get("PUBECOSPHERE_LLM_API_KEY", ""),
            "model": os.environ.get(ENV_MODEL, DEFAULT_MODEL),
            "max_tokens": int(os.environ.get(ENV_MAX_TOKENS, str(DEFAULT_MAX_TOKENS))),
            "timeout": float(os.environ.get("PUBECOSPHERE_LLM_TIMEOUT", "120")),
            "max_retries": int(os.environ.get("PUBECOSPHERE_LLM_MAX_RETRIES", "3")),
            "backoff": float(os.environ.get("PUBECOSPHERE_LLM_BACKOFF", "5")),
            "prompt_dir": prompt_dir
            or os.environ.get("PUBECOSPHERE_LLM_PROMPT_DIR", str(PROMPT_DIR_DEFAULT)),
        }
        # 只保留 dataclass 定义的字段，避免多传未知键。
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in vals.items() if k in names})
