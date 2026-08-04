"""深度信号提取：从评论（comments 表）提取 stage-2 需要的信号。

- 作者回应 / 撤稿声明 / 证据链接 / 多轮交锋 / 独立评论者 / 知名打假人 / 图片 / 时间跨度
对线关键词（fabricat/fraud…）在 PubPeer 评论中普遍出现、区分度低，v1 不做，保留注释备查。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from . import config

_URL_RE = re.compile(r"https?://[^\s)\"']+")
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s)\"']+")
_ROUND_RE = re.compile(r"#\s*(\d+)")


def matches_sleuth(name: str, sleuths: tuple) -> bool:
    """姓氏匹配：评论者姓 == 某打假人姓（如 'Elisabeth M Bik' ↔ 'Elisabeth Bik'）。

    连续子串匹配会被中间名缩写（m）挡住，故按姓氏判断，对知名打假人足够可靠。
    """
    n = (name or "").strip().lower()
    if not n:
        return False
    tokens = [t for t in re.split(r"[^a-z]+", n) if t]
    if not tokens:
        return False
    last = tokens[-1]
    if len(last) < 3:
        return False
    for sl in sleuths:
        sl_tokens = [t for t in re.split(r"[^a-z]+", sl.lower()) if t]
        if sl_tokens and len(sl_tokens[-1]) >= 3 and sl_tokens[-1] == last:
            return True
    return False


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def normalize_user(c: dict) -> str:
    """独立评论者标识：优先 user_alias，其次 user_name，缺省匿名。"""
    u = (c.get("user_alias") or "").strip()
    if not u:
        u = (c.get("user_name") or "").strip()
    return u or "匿名"


def extract_comment_signals(comments: list[dict], sleuths: tuple = config.ScoringConfig.sleuths) -> dict:
    """从完整评论列表提取 stage-2 原始信号。comments 为空时返回全 0。"""
    if not comments:
        return {
            "n_comments": 0, "author_response": 0, "retraction_eoc": 0,
            "rounds": 0, "n_users": 0, "has_sleuth": 0, "has_image": 0,
            "n_links": 0, "span_days": 0.0, "commenters": [],
        }

    n_comments = len(comments)
    author_response = 0
    retraction_eoc = 0
    rounds = 0
    has_image = 0
    n_links = 0
    users: set[str] = set()
    has_sleuth = 0
    accepted = []

    for c in comments:
        if c.get("is_from_author"):
            author_response = 1
        md = c.get("markdown") or ""
        low = md.lower()
        if any(m in low for m in config.ScoringConfig.retraction_markers):
            retraction_eoc = 1
        if "![" in md or "<img" in low:
            has_image = 1
        n_links += len(_URL_RE.findall(md)) + len(_DOI_RE.findall(md))
        users.add(normalize_user(c))
        if not has_sleuth and matches_sleuth(c.get("user_name"), sleuths):
            has_sleuth = 1
        for ref in _ROUND_RE.findall(md):
            try:
                rounds = max(rounds, int(ref))
            except ValueError:
                pass
        t = _dt(c.get("accepted_at"))
        if t:
            accepted.append(t)

    span_days = 0.0
    if len(accepted) >= 2:
        span_days = (max(accepted) - min(accepted)).total_seconds() / 86400.0

    return {
        "n_comments": n_comments,
        "author_response": author_response,
        "retraction_eoc": retraction_eoc,
        "rounds": rounds,
        "n_users": len(users),
        "has_sleuth": has_sleuth,
        "has_image": has_image,
        "n_links": n_links,
        "span_days": span_days,
        "commenters": sorted(users),
    }
