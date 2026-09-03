"""SQLite 存储：捕获记录 / 文章 / 评论。

设计要点：
- 幂等：capture 与评论均 upsert，重复运行不产生脏数据，可断点续跑。
- 累积：captures 表记录每次 feed 捕获；publications/comments 由回访填充。
- 数据目录（默认 data/）应在 .gitignore 中，服务器 git pull 不会覆盖。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS captures (
    pubpeer_id          TEXT PRIMARY KEY,
    int_id              INTEGER,
    title               TEXT,
    journal             TEXT,
    issn                TEXT,
    last_commented      TEXT,            -- ISO UTC
    comments_total      INTEGER,
    has_author_response INTEGER,
    captured_at         TEXT,            -- ISO UTC，最近一次捕获时间
    revisited_at        TEXT             -- ISO UTC，最近一次回访时间
);

CREATE TABLE IF NOT EXISTS publications (
    pubpeer_id          TEXT PRIMARY KEY,
    int_id              INTEGER,
    title               TEXT,
    abstract            TEXT,
    authors             TEXT,            -- JSON 数组
    journal             TEXT,
    issn                TEXT,
    doi                 TEXT,
    published_at        TEXT,
    created             TEXT,            -- ISO UTC
    last_commented      TEXT,            -- ISO UTC
    comments_total      INTEGER,
    has_author_response INTEGER,
    url                 TEXT,
    fetched_at          TEXT             -- ISO UTC
);

CREATE TABLE IF NOT EXISTS comments (
    id                  INTEGER PRIMARY KEY,   -- PubPeer 全局评论 id
    pubpeer_id          TEXT NOT NULL,
    inner_id            INTEGER,
    markdown            TEXT,
    user_alias          TEXT,
    user_name           TEXT,
    is_from_author      INTEGER,
    accepted_at         TEXT                   -- ISO UTC，评论发布时间
);
CREATE INDEX IF NOT EXISTS idx_comments_pubpeer ON comments(pubpeer_id);
CREATE INDEX IF NOT EXISTS idx_comments_accepted ON comments(accepted_at);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # -- captures ------------------------------------------------------------

    def upsert_captures(self, records: list[dict], captured_at: str) -> int:
        """写入/更新捕获记录，返回条数。"""
        with self.tx() as c:
            c.executemany(
                """
                INSERT INTO captures
                    (pubpeer_id, int_id, title, journal, issn, last_commented,
                     comments_total, has_author_response, captured_at)
                VALUES
                    (:pubpeer_id, :int_id, :title, :journal, :issn, :last_commented,
                     :comments_total, :has_author_response, :captured_at)
                ON CONFLICT(pubpeer_id) DO UPDATE SET
                    int_id=excluded.int_id, title=excluded.title, journal=excluded.journal,
                    issn=excluded.issn, last_commented=excluded.last_commented,
                    comments_total=excluded.comments_total,
                    has_author_response=excluded.has_author_response,
                    captured_at=excluded.captured_at
                """,
                [dict(r, captured_at=captured_at) for r in records],
            )
        return len(records)

    def captures_for_revisit(
        self, since: str | None = None, until: str | None = None,
        limit: int | None = None, force: bool = False, min_comments: int = 0,
    ) -> list[dict]:
        """待回访的捕获记录。

        默认选取 7 天前捕获、且 7 天内未回访过的记录（`--force` 则无视回访时间）；
        `until` 给定 captured_at 上界（首现窗口，如只回访 8.1-8.7 期候选）；
        `min_comments > 0` 时只取评论数达到该阈值的文章。
        """
        q = "SELECT * FROM captures WHERE 1=1"
        params: list = []
        if since is not None:
            q += " AND captured_at >= ?"
            params.append(since)
        if until is not None:
            q += " AND captured_at < ?"
            params.append(until)
        if min_comments > 0:
            q += " AND comments_total >= ?"
            params.append(min_comments)
        if not force:
            q += " AND (revisited_at IS NULL OR revisited_at < ?)"
            params.append(since)
        q += " ORDER BY captured_at DESC"
        if limit:
            q += " LIMIT ?"
            params.append(limit)
        cur = self.conn.execute(q, params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    def mark_revisited(self, pubpeer_id: str, at: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE captures SET revisited_at=? WHERE pubpeer_id=?", (at, pubpeer_id))

    # -- publications / comments -------------------------------------------

    def upsert_publication(self, pub: dict, fetched_at: str) -> None:
        with self.tx() as c:
            c.execute(
                """
                INSERT INTO publications
                    (pubpeer_id, int_id, title, abstract, authors, journal, issn, doi,
                     published_at, created, last_commented, comments_total,
                     has_author_response, url, fetched_at)
                VALUES
                    (:pubpeer_id, :int_id, :title, :abstract, :authors, :journal, :issn, :doi,
                     :published_at, :created, :last_commented, :comments_total,
                     :has_author_response, :url, :fetched_at)
                ON CONFLICT(pubpeer_id) DO UPDATE SET
                    int_id=excluded.int_id, title=excluded.title, abstract=excluded.abstract,
                    authors=excluded.authors, journal=excluded.journal, issn=excluded.issn,
                    doi=excluded.doi, published_at=excluded.published_at, created=excluded.created,
                    last_commented=excluded.last_commented,
                    comments_total=excluded.comments_total,
                    has_author_response=excluded.has_author_response, url=excluded.url,
                    fetched_at=excluded.fetched_at
                """,
                dict(pub, fetched_at=fetched_at),
            )

    def upsert_comments(self, pubpeer_id: str, comments: list[dict]) -> int:
        with self.tx() as c:
            c.executemany(
                """
                INSERT INTO comments
                    (id, pubpeer_id, inner_id, markdown, user_alias, user_name,
                     is_from_author, accepted_at)
                VALUES
                    (:id, :pubpeer_id, :inner_id, :markdown, :user_alias, :user_name,
                     :is_from_author, :accepted_at)
                ON CONFLICT(id) DO UPDATE SET
                    inner_id=excluded.inner_id, markdown=excluded.markdown,
                    user_alias=excluded.user_alias, user_name=excluded.user_name,
                    is_from_author=excluded.is_from_author, accepted_at=excluded.accepted_at
                """,
                comments,
            )
        return len(comments)

    # -- 查询 ---------------------------------------------------------------

    def all_comments_with_publications(self) -> list[dict]:
        cur = self.conn.execute(
            """
            SELECT c.*, p.title AS pub_title, p.journal AS pub_journal, p.doi AS pub_doi,
                   p.authors AS pub_authors, p.url AS pub_url, p.published_at AS pub_published_at,
                   p.last_commented AS pub_last_commented
            FROM comments c
            LEFT JOIN publications p ON p.pubpeer_id = c.pubpeer_id
            WHERE c.accepted_at IS NOT NULL
            ORDER BY c.accepted_at
            """
        )
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    def all_publications(self) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM publications ORDER BY fetched_at")
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    def stats(self) -> dict:
        def n(sql: str) -> int:
            return self.conn.execute(sql).fetchone()[0]

        return {
            "captures": n("SELECT COUNT(*) FROM captures"),
            "publications": n("SELECT COUNT(*) FROM publications"),
            "comments": n("SELECT COUNT(*) FROM comments"),
        }
