"""PubPeer HTTP 客户端：浏览器 UA、限速、退避重试 + 各接口 URL。

PubPeer 对默认 curl UA 的连接会挂起，且对高频请求返回 403。
本客户端统一注入浏览器 UA，请求间保持限速，对瞬时错误做退避重试。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

RETRYABLE = {429, 500, 502, 503, 504}   # 瞬时错误，可退避重试


class FeedCappedError(RuntimeError):
    """`/api/recent` 偏移超过上限(约400条)时抛出，表示已无法继续回溯。"""


@dataclass
class ClientConfig:
    delay: float = 1.5          # 相邻请求间隔（秒）
    timeout: float = 30.0       # 单次请求超时
    max_retries: int = 3        # 瞬时错误重试次数
    backoff: float = 5.0        # 重试基础退避秒数
    _last_request: float = field(default=0.0, repr=False)


class PubPeerClient:
    def __init__(self, config: Optional[ClientConfig] = None):
        self.config = config or ClientConfig()

    # -- 基础请求 -----------------------------------------------------------

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self.config._last_request
        wait = self.config.delay - elapsed
        if wait > 0:
            time.sleep(wait)
        self.config._last_request = time.monotonic()

    def _request(self, url: str, accept: str, data: Optional[bytes] = None) -> bytes:
        last_exc: Optional[Exception] = None
        for attempt in range(self.config.max_retries + 1):
            self._throttle()
            headers = {
                "User-Agent": USER_AGENT,
                "Accept": accept,
                "Accept-Language": "en-US,en;q=0.9",
            }
            if data is not None:
                headers["Content-Type"] = "application/json;charset=UTF-8"
            req = urllib.request.Request(url, data=data, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                    return resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code in RETRYABLE and attempt < self.config.max_retries:
                    last_exc = exc
                    time.sleep(self.config.backoff * (attempt + 1))
                    continue
                raise
            except (urllib.error.URLError, OSError) as exc:
                last_exc = exc
                if attempt < self.config.max_retries:
                    time.sleep(self.config.backoff * (attempt + 1))
                    continue
                break
        raise RuntimeError(f"request failed after retries: {url}") from last_exc

    def get_json(self, url: str) -> dict:
        return json.loads(self._request(url, accept="application/json").decode("utf-8"))

    def get_html(self, url: str) -> str:
        return self._request(url, accept="text/html,application/xhtml+xml").decode("utf-8", errors="ignore")

    def get_binary(self, url: str) -> bytes:
        """下载任意二进制内容（评论图片等）。"""
        return self._request(url, accept="image/*,image/webp,*/*")

    # -- 各接口 -------------------------------------------------------------

    def recent_feed(self, offset: int) -> list[dict]:
        """`/api/recent/from/{offset}`，offset 合法范围 0..400（约 40 条/页）。"""
        url = f"https://pubpeer.com/api/recent/from/{offset}"
        return self.get_json(url).get("publications", [])

    def publication_page(self, pubpeer_id: str) -> str:
        return self.get_html(f"https://pubpeer.com/publications/{pubpeer_id}")

    def v3_publications(self, dois: list[str], devkey: str = "PubPeerZotero") -> dict:
        """官方 v3 API：POST DOI 列表（≤40/批）→ 返回反馈摘要（评论数/用户/期刊等）。"""
        url = f"https://pubpeer.com/v3/publications?devkey={devkey}"
        body = json.dumps({"dois": dois}).encode("utf-8")
        raw = self._request(url, accept="application/json", data=body)
        return json.loads(raw.decode("utf-8"))
