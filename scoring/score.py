"""两阶段打分：stage-1（全部捕获，廉价）→ stage-2（短名单，真实评论）。

每维度先归一化到 [0,1]，按 config 权重加权；stage-2 是 stage-1 的「激烈交互」
细化，融合 final = α·stage2 + (1-α)·stage1。
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from . import config
from .signals import extract_comment_signals, matches_sleuth

_PARTITION_POINTS = {1: 1.0, 2: 0.75, 3: 0.5, 4: 0.25}
_CCF_POINTS = {"A": 1.0, "B": 0.8, "C": 0.6, "T1": 1.0, "T2": 0.8, "T3": 0.6}


def _norm_log(n: float, baseline: float) -> float:
    if n <= 0:
        return 0.0
    return min(1.0, math.log10(n + 1) / math.log10(baseline + 1))


def _days_since(iso_str: str | None) -> float | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)


def journal_impact_score(jinfo: dict) -> float:
    """期刊影响力：JCR IF(2025) → CAS 大类分区 → CCF 等级。"""
    if_ = jinfo.get("impact_factor")
    if if_:
        return min(1.0, math.log10(if_ + 1) / math.log10(11.0))
    part = jinfo.get("partition") or 0
    if part:
        base = _PARTITION_POINTS.get(part, 0.1)
        if jinfo.get("top"):
            base = min(1.0, base + 0.1)
        return base
    grade = jinfo.get("ccf_grade") or ""
    if grade:
        return _CCF_POINTS.get(grade, 0.1)
    return 0.1


def _name_forms(s: str) -> set[str]:
    """作者/评论者名的模糊形式：全名小写 + 姓。"""
    parts = re.split(r"\s+", (s or "").strip().lower())
    parts = [p for p in parts if p]
    out = {s.lower()} if s else set()
    if parts:
        out.add(parts[-1])                      # 姓
        if len(parts) >= 2:
            out.add(" ".join(parts[:2]))        # 名+姓
    return {p for p in out if len(p) >= 3}


def _author_overlap(v3_users: list[str], authors: list[str]) -> float:
    if not v3_users or not authors:
        return 0.0
    author_forms = set()
    for a in authors:
        author_forms |= _name_forms(a)
    for u in v3_users:
        if _name_forms(u) & author_forms:
            return 1.0
    return 0.0


def stage1_features(capture: dict, jinfo: dict, feedback: dict | None,
                    cfg, pub_authors: list[str]) -> dict:
    """stage-1 归一化特征。feedback 为 v3_feedback 行（可能为空 dict）。"""
    fb = feedback or {}
    comments_total = max(capture.get("comments_total") or 0, fb.get("total_comments") or 0)
    users = [(u or "").strip() for u in (fb.get("users") or "").split(",") if u and u.strip()]
    sleuth = 1.0 if any(matches_sleuth(u, cfg.sleuths) for u in users) else 0.0
    last = capture.get("last_commented") or fb.get("last_commented_at")
    days = _days_since(last)
    recency = max(0.0, 1.0 - days / cfg.recency_halflife_days) if days is not None else 0.0

    return {
        "journal_impact": journal_impact_score(jinfo),
        "comment_volume": _norm_log(comments_total, cfg.comment_baseline),
        "recency": recency,
        "sleuth": sleuth,
        "distinct_commenters": min(1.0, len(users) / cfg.distinct_baseline),
        "author_overlap": _author_overlap(users, pub_authors),
        "journal_alert": 1.0 if jinfo.get("journal_alert") else 0.0,
    }


def stage1_score(feats: dict, cfg) -> float:
    return sum(cfg.stage1_weights[k] * feats[k] for k in cfg.stage1_weights)


def stage2_features(comments: list[dict], jinfo: dict, cfg) -> dict:
    sig = extract_comment_signals(comments, cfg.sleuths)
    return {
        "author_response": float(sig["author_response"]),
        "retraction_eoc": float(sig["retraction_eoc"]),
        "rounds": min(1.0, sig["rounds"] / cfg.rounds_baseline),
        "sleuth_gt": float(sig["has_sleuth"]),
        "comment_volume_gt": _norm_log(sig["n_comments"], cfg.comment_baseline),
        "journal_impact": journal_impact_score(jinfo),
        "evidence_links": min(1.0, sig["n_links"] / cfg.links_baseline),
        "thread_span": min(1.0, sig["span_days"] / cfg.span_baseline_days),
        "distinct_commenters_gt": min(1.0, sig["n_users"] / cfg.distinct_baseline),
        "images": float(sig["has_image"]),
    }


def stage2_score(feats: dict, cfg) -> float:
    return sum(cfg.stage2_weights[k] * feats[k] for k in cfg.stage2_weights)


def apply_category_interest(score: float, major: str, cfg) -> float:
    mult = cfg.category_interest.get(major, 1.0) if cfg.category_interest else 1.0
    return score * mult


def blend(s1: float, s2: float, cfg) -> float:
    return cfg.blend_stage2 * s2 + (1 - cfg.blend_stage2) * s1
