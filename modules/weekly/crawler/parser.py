"""解析 PubPeer 数据：feed 条目、文章页内嵌的 data-publication / data-comments。

文章页 `<publication-page>` 组件把数据以 HTML 转义 JSON 的形式内嵌在属性里：
- `data-publication="{...}"` — 文章元数据（标题/作者/期刊/日期）
- `data-comments="[{...}]"`   — 完整评论线程（markdown/用户/是否作者回应/accepted_at）
"""
from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any

from . import dates

_ATTR_RE = re.compile(r'data-(publication|comments)="([^"]*)"')
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<&,;]+")
_TAG_RE = re.compile(r"<[^>]+>")


def _extract_attr(html: str, name: str) -> Any | None:
    """按属性名精确匹配，避免 data-publication 先于 data-comments 出现导致错取。"""
    m = re.search(rf'data-{name}="([^"]*)"', html)
    if not m:
        return None
    return json.loads(html_lib.unescape(m.group(1)))


def _strip_tags(text: str | None) -> str | None:
    if not text:
        return text
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text)).strip()


def _extract_doi(html: str) -> str | None:
    m = _DOI_RE.search(html)
    return m.group(0) if m else None


def _parse_flexible(s: str | None) -> str | None:
    """兼容 feed 格式与 ISO 格式，统一转 ISO；都失败则返回 None。"""
    if not s:
        return None
    for parser in (dates.parse_feed_date, dates.parse_iso):
        try:
            return dates.to_iso(parser(s))
        except ValueError:
            continue
    return None


def _authors_to_names(authors: list[dict] | None) -> list[str]:
    out = []
    for a in authors or []:
        name = a.get("display_name") or f"{a.get('first_name', '')} {a.get('last_name', '')}".strip()
        if name:
            out.append(name)
    return out


# -- feed 条目 ---------------------------------------------------------------

def parse_feed_item(item: dict) -> dict:
    """把 `/api/recent` 一条记录转成 captures 表的行（捕获时即知元数据）。"""
    journals = (item.get("journals") or {}).get("data") or []
    journal = journals[0] if journals else {}
    return {
        "pubpeer_id": item.get("pubpeer_id"),
        "int_id": item.get("id"),
        "title": item.get("title"),
        "journal": journal.get("title"),
        "issn": journal.get("issn"),
        "last_commented": dates.to_iso(dates.parse_feed_date(item.get("last_commented"))),
        "comments_total": item.get("comments_total") or 0,
        "has_author_response": 1 if item.get("has_author_response") else 0,
    }


# -- 文章页 ---------------------------------------------------------------

def parse_publication_page(html: str) -> tuple[dict, list[dict]]:
    """从文章页提取 (publication, comments)。"""
    pub_raw = _extract_attr(html, "publication") or {}
    comments_raw = _extract_attr(html, "comments") or []

    journals = pub_raw.get("journals") or []
    journal = journals[0] if journals else {}
    journal_title = html_lib.unescape(journal.get("title"))  # data-publication 为双重转义

    authors = _authors_to_names(pub_raw.get("authors"))
    pub = {
        "pubpeer_id": pub_raw.get("pubpeer_id"),
        "int_id": pub_raw.get("id"),
        "title": pub_raw.get("title"),
        "abstract": _strip_tags(pub_raw.get("abstract")),
        "authors": json.dumps(authors, ensure_ascii=False),
        "journal": journal_title,
        "issn": journal.get("issn"),
        "doi": _extract_doi(html),
        "published_at": pub_raw.get("published_at"),
        "created": _parse_flexible(pub_raw.get("created")),
        "last_commented": _parse_flexible(pub_raw.get("last_commented")),
        "comments_total": pub_raw.get("comments_total") or 0,
        "has_author_response": 1 if pub_raw.get("has_author_response") else 0,
        "url": pub_raw.get("url") or (f"/publications/{pub_raw.get('pubpeer_id')}" if pub_raw.get("pubpeer_id") else None),
    }

    comments = []
    for c in comments_raw:
        user = c.get("user") or {}
        comments.append({
            "id": c.get("id"),
            "pubpeer_id": pub["pubpeer_id"],
            "inner_id": c.get("inner_id"),
            "markdown": c.get("markdown"),
            "user_alias": c.get("user_alias"),
            "user_name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or None,
            "is_from_author": 1 if c.get("is_from_author") else 0,
            "accepted_at": dates.to_iso(dates.parse_iso(c.get("accepted_at"))),
        })
    return pub, comments
