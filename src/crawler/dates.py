"""日期解析与归一化。

PubPeer 里出现两种日期格式：
- feed 的 `last_commented`/`created`：`"Tue, Aug 4, 2026 1:05 AM"`（无时区，站点内部按 UTC）
- 评论的 `accepted_at`：`"2026-08-04T01:05:08.000000Z"`（ISO 8601 UTC）

统一归一化为固定宽度的 UTC ISO 字符串 `YYYY-MM-DDTHH:MM:SSZ`，供存储与比较。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

FEED_FMT = "%a, %b %d, %Y %I:%M %p"   # Tue, Aug 4, 2026 1:05 AM
ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().strftime(ISO_FMT)


def parse_feed_date(s: str | None) -> datetime | None:
    """解析 feed 的 `Tue, Aug 4, 2026 1:05 AM` 为 aware UTC。"""
    if not s:
        return None
    dt = datetime.strptime(s, FEED_FMT)
    return dt.replace(tzinfo=timezone.utc)


def parse_iso(s: str | None) -> datetime | None:
    """解析 `2026-08-04T01:05:08.000000Z` 为 aware UTC。"""
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1]
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = datetime.fromisoformat(s.split(".")[0])
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_iso(dt: datetime | None) -> str | None:
    return dt.strftime(ISO_FMT) if dt else None


def days_ago(days: int) -> str:
    return (utcnow() - timedelta(days=days)).strftime(ISO_FMT)
