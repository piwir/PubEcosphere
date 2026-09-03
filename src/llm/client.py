"""OpenAI 兼容 `chat/completions` 客户端（纯 stdlib urllib 实现）。

无第三方依赖：
- 请求体按 OpenAI 消息格式构造（纯文本消息）。
- `transport` 可注入（默认走 urllib），离线自测用假 transport 验 POST body 即可。
- 5xx / 429 退避重试，参照 src/crawler/client.py 的 RETRYABLE 先例。
未来若想换 openai SDK，只改本文件。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from .config import LLMConfig

RETRYABLE = {429, 500, 502, 503, 504}  # 瞬时错误，可退避重试

Transport = Callable[[str, dict, dict, float], tuple[int, dict]]


def default_transport(url: str, payload: dict, headers: dict, timeout: float) -> tuple[int, dict]:
    """真实 HTTP 实现：POST JSON → (status, parsed_body)。"""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise LLMError(f"非 JSON 响应（{resp.status}）：{body[:200]!r}") from exc
    return resp.status, parsed


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, config: LLMConfig, transport: Optional[Transport] = None):
        self.config = config
        self._transport = transport or default_transport

    def chat(self, messages: list[dict], model: str | None = None,
             temperature: float = 0.7, max_tokens: int | None = None) -> str:
        """发送一轮对话，返回助手消息文本。model 缺省用 config.model。"""
        payload: dict[str, Any] = {
            "model": model or self.config.model,
            "messages": messages,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        last: Any = None
        for attempt in range(self.config.max_retries + 1):
            try:
                status, data = self._transport(url, payload, headers, self.config.timeout)
            except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
                last = exc
                if attempt < self.config.max_retries:
                    time.sleep(self.config.backoff * (attempt + 1))
                    continue
                raise LLMError(f"request failed: {exc}") from exc
            if status in RETRYABLE and attempt < self.config.max_retries:
                last = status
                time.sleep(self.config.backoff * (attempt + 1))
                continue
            if status != 200:
                raise LLMError(f"chat/completions returned {status}: {data!r}")
            try:
                choice = data["choices"][0]
                finish = choice.get("finish_reason")
                content = choice["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise LLMError(f"unexpected response shape: {data!r}") from exc
            if finish == "length":
                # 输出被 max_tokens 截断：静默返回残缺草稿比报错更糟。
                raise LLMError(
                    f"输出被 max_tokens 截断（finish_reason=length，已收 {len(content)} 字）。"
                    "请调大 PUBECOSPHERE_LLM_MAX_TOKENS（deepseek-v4-flash 上限 384K）。")
            return content
        raise LLMError(f"retries exhausted, last: {last!r}")
