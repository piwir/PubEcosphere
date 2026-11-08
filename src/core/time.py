"""UTC 时间与 ISO 字符串：全仓统一的时间口径。

库里所有时间列（`captured_at` / `fetched_at` / `accepted_at` / `picked_at` …）都是
**固定宽度 UTC ISO**（`YYYY-MM-DDTHH:MM:SSZ`），可直接按字符串比较排序。
不要各包自写 `datetime.now(timezone.utc).strftime(...)`——口径漂了就排序错。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def utcnow() -> datetime:
    """当前 UTC 时间（aware）。"""
    return datetime.now(timezone.utc)


def iso_now() -> str:
    """当前 UTC 时间 → `YYYY-MM-DDTHH:MM:SSZ`。"""
    return utcnow().strftime(ISO_FMT)


def to_iso(dt: datetime | None) -> str | None:
    """aware datetime → 固定宽度 ISO 字符串；None 原样返回。"""
    return dt.strftime(ISO_FMT) if dt else None


def days_ago(days: int) -> str:
    """`days` 天前的固定宽度 ISO 字符串。"""
    return (utcnow() - timedelta(days=days)).strftime(ISO_FMT)


def parse_iso(s: str | None) -> datetime | None:
    """ISO 字符串（容忍 `Z`、微秒、无时区）→ aware UTC；不可解析返回 None。"""
    if not s:
        return None
    raw = s.strip()
    if raw.endswith("Z"):
        raw = raw[:-1]
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
