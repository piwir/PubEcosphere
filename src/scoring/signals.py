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


def _norm_name(s: str) -> str:
    """名字归一：小写 + 非字母数字（标点/连字符/空白/全角字符）折叠为单个空格。

    用于打假人名单的全等匹配，容忍 'Elisabeth M. Bik' 与 'Elisabeth M Bik'、
    'Yi‐ming Yuan'（U+2010 连字符）等同一人的书写差异。
    """
    return re.sub(r"[^a-z0-9]+", " ", (s or "").strip().lower()).strip()


def matches_sleuth(name: str, sleuths: tuple) -> bool:
    """精确别名匹配：评论者显示名（别名/真名）与打假人名单归一后全等。

    名单存「真名/马甲」的精确键（如 'Elisabeth M Bik'、'Hoya Camphorifolia'），
    只做书写归一、不做姓氏模糊，避免同姓（如 Ben-David 撞 Sholto David）误判。
    """
    n = _norm_name(name)
    if not n:
        return False
    return any(_norm_name(s) == n for s in sleuths)


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
        if not has_sleuth and (
            matches_sleuth(c.get("user_alias"), sleuths)
            or matches_sleuth(c.get("user_name"), sleuths)
        ):
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
