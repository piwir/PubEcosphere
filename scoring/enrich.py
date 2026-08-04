"""富集：CrossRef DOI 解析 + PubPeer v3 批量获取，均有缓存。

单条失败不中断批次；缺 DOI/feedback 时上层信号回退（0 / captures.comments_total）。
"""
from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timezone

from .cas import issn_key, name_key

# CrossRef 礼貌池标识
_CROSSREF_MAILTO = "pubecosphere@example.com"


def _normalize_v3_time(s: str | None) -> str | None:
    """v3 的 last_commented_at 是空格格式 "2026-08-04 02:38:35"，统一转 ISO。"""
    if not s:
        return None
    s = s.strip()
    if "T" in s:                       # 已是 ISO
        return s if s.endswith("Z") else s + "Z"
    return s.replace(" ", "T") + "Z"


# ---- DOI 解析 -------------------------------------------------------------

def _crossref_search(client, title: str, issn: str | None, journal: str | None) -> list[dict]:
    params: dict = {"rows": "3", "mailto": _CROSSREF_MAILTO}
    if issn:
        params["query.title"] = title
        params["filter"] = f"issn:{issn}"
    else:
        params["query.bibliographic"] = title
        if journal:
            params["query.container-title"] = journal
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    try:
        raw = client._request(url, accept="application/json")
    except Exception:                   # noqa: BLE001 —— 单条失败返回空
        return []
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    return data.get("message", {}).get("items", []) or []


def _first(v) -> str | None:
    """CrossRef 的 title/container-title 是数组，取首个字符串。"""
    if isinstance(v, list):
        return v[0] if v else None
    return v or None


def _validate(cand: dict, issn: str | None, journal: str | None) -> bool:
    if issn:
        c_issns = cand.get("ISSN") or []
        if any(issn_key(x) == issn_key(issn) for x in c_issns):
            return True
    if journal:
        cj = _first(cand.get("container-title"))
        ck, jk = name_key(cj), name_key(journal)
        if ck and jk and (ck == jk or ck in jk or jk in ck):
            return True
    return False


def resolve_doi(client, capture: dict, pub_doi_map: dict[str, str]) -> tuple:
    """返回 (doi, method, crossref_title, crossref_journal, journal_match)。"""
    pid = capture["pubpeer_id"]
    if pid in pub_doi_map and pub_doi_map[pid]:
        return pub_doi_map[pid], "publications", None, None, 1

    title = capture.get("title")
    if not title:
        return None, "crossref", None, None, 0
    issn = capture.get("issn")
    journal = capture.get("journal")
    candidates = _crossref_search(client, title, issn, journal)
    for cand in candidates:
        doi = cand.get("DOI")
        if doi and _validate(cand, issn, journal):
            return doi, "crossref", _first(cand.get("title")), _first(cand.get("container-title")), 1
    return None, "crossref", None, None, 0


# ---- v3 反馈 --------------------------------------------------------------

def fetch_feedback(client, store, dois: list[str], ttl_days: int, verbose: bool = False) -> int:
    """批量拉取缺失/过期的 v3 feedback，返回新拉取条数。"""
    missing = store.missing_v3(dois, ttl_days)
    if not missing:
        return 0
    fetched = 0
    for i in range(0, len(missing), 40):
        batch = missing[i:i + 40]
        try:
            resp = client.v3_publications(batch)
        except Exception as exc:        # noqa: BLE001 —— 单批失败不中断
            print(f"  v3 batch {i} failed: {exc}", flush=True)
            continue
        for fb in resp.get("feedbacks", []):
            store.upsert_v3_feedback({
                "doi": fb.get("id"),
                "total_comments": fb.get("total_comments"),
                "users": fb.get("users"),
                "last_commented_at": _normalize_v3_time(fb.get("last_commented_at")),
                "url": fb.get("url"),
                "title": fb.get("title"),
                "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            fetched += 1
        if verbose:
            print(f"  v3 batch {i // 40 + 1}: +{len(batch)} dois", flush=True)
    return fetched
