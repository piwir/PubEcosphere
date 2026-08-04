"""打分模块 SQLite 存储：在 data/pubpeer.db 里建自己命名空间的表。

与 crawler/store.py 并存：只 CREATE 自己的表（IF NOT EXISTS），
不改动 captures/publications/comments；同时提供对爬虫表的只读查询。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS doi_resolution (
    pubpeer_id      TEXT PRIMARY KEY,
    doi             TEXT,
    method          TEXT,            -- publications | crossref
    crossref_title  TEXT,
    crossref_journal TEXT,
    journal_match   INTEGER,
    resolved_at     TEXT
);
CREATE TABLE IF NOT EXISTS v3_feedback (
    doi             TEXT PRIMARY KEY,
    total_comments  INTEGER,
    users           TEXT,
    last_commented_at TEXT,
    url             TEXT,
    title           TEXT,
    fetched_at      TEXT
);
CREATE TABLE IF NOT EXISTS scores (
    run_id          TEXT,
    pubpeer_id      TEXT,
    major           TEXT,
    minor           TEXT,
    minor_partition INTEGER,
    partition       INTEGER,
    top             INTEGER,
    impact_factor   REAL,
    ccf_grade       TEXT,
    journal_alert   TEXT,
    stage           INTEGER,
    final_score     REAL,
    breakdown       TEXT,            -- JSON
    PRIMARY KEY (run_id, pubpeer_id)
);
CREATE TABLE IF NOT EXISTS shortlist (
    run_id          TEXT,
    pubpeer_id      TEXT,
    major           TEXT,
    stage1_score    REAL,
    stage2_score    REAL,
    final_score     REAL,
    PRIMARY KEY (run_id, pubpeer_id)
);
"""


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rows(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


class ScoringStore:
    """打分模块存储：自有表 + 对爬虫表（captures/publications/comments）的只读访问。"""

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """旧库升级：scores 缺 minor_partition 列时补上。"""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(scores)")}
        if "minor_partition" not in cols:
            self.conn.execute("ALTER TABLE scores ADD COLUMN minor_partition INTEGER DEFAULT 0")
            self.conn.commit()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ---- doi_resolution ---------------------------------------------------

    def upsert_doi_resolution(self, pubpeer_id: str, doi: str | None, method: str,
                              title: str | None = None, journal: str | None = None,
                              journal_match: int | None = None) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO doi_resolution
                       (pubpeer_id, doi, method, crossref_title, crossref_journal, journal_match, resolved_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(pubpeer_id) DO UPDATE SET
                       doi=excluded.doi, method=excluded.method,
                       crossref_title=excluded.crossref_title,
                       crossref_journal=excluded.crossref_journal,
                       journal_match=excluded.journal_match, resolved_at=excluded.resolved_at""",
                (pubpeer_id, doi, method, title, journal, journal_match, _iso_now()),
            )

    def doi_for(self, pubpeer_ids: list[str]) -> dict[str, str]:
        if not pubpeer_ids:
            return {}
        q = ",".join("?" * len(pubpeer_ids))
        cur = self.conn.execute(
            f"SELECT pubpeer_id, doi FROM doi_resolution WHERE pubpeer_id IN ({q})", pubpeer_ids)
        return {r["pubpeer_id"]: r["doi"] for r in _rows(cur) if r["doi"]}

    # ---- v3_feedback ------------------------------------------------------

    def upsert_v3_feedback(self, feedback: dict) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO v3_feedback
                       (doi, total_comments, users, last_commented_at, url, title, fetched_at)
                   VALUES (:doi,:total_comments,:users,:last_commented_at,:url,:title,:fetched_at)
                   ON CONFLICT(doi) DO UPDATE SET
                       total_comments=excluded.total_comments, users=excluded.users,
                       last_commented_at=excluded.last_commented_at, url=excluded.url,
                       title=excluded.title, fetched_at=excluded.fetched_at""",
                feedback,
            )

    def v3_feedback(self, dois: list[str]) -> dict[str, dict]:
        if not dois:
            return {}
        q = ",".join("?" * len(dois))
        cur = self.conn.execute(
            f"SELECT * FROM v3_feedback WHERE doi IN ({q})", dois)
        return {r["doi"]: r for r in _rows(cur)}

    def missing_v3(self, dois: list[str], ttl_days: int) -> list[str]:
        """需要重新拉取的 DOI：无缓存，或缓存超时。"""
        if not dois:
            return []
        q = ",".join("?" * len(dois))
        cur = self.conn.execute(
            f"SELECT doi, fetched_at FROM v3_feedback WHERE doi IN ({q})", dois)
        fresh: set[str] = set()
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
        for r in _rows(cur):
            try:
                ft = datetime.fromisoformat(r["fetched_at"].replace("Z", "+00:00"))
                if ft >= cutoff:
                    fresh.add(r["doi"])
            except ValueError:
                continue
        return [d for d in dois if d not in fresh]

    # ---- scores / shortlist（run 级） ------------------------------------

    def upsert_scores(self, run_id: str, rows: list[dict]) -> None:
        with self.tx() as c:
            c.executemany(
                """INSERT INTO scores
                       (run_id, pubpeer_id, major, minor, minor_partition, partition, top,
                        impact_factor, ccf_grade, journal_alert, stage, final_score, breakdown)
                   VALUES
                       (:run_id,:pubpeer_id,:major,:minor,:minor_partition,:partition,:top,
                        :impact_factor,:ccf_grade,:journal_alert,:stage,:final_score,:breakdown)
                   ON CONFLICT(run_id, pubpeer_id) DO UPDATE SET
                       major=excluded.major, minor=excluded.minor,
                       minor_partition=excluded.minor_partition, partition=excluded.partition,
                       top=excluded.top, impact_factor=excluded.impact_factor,
                       ccf_grade=excluded.ccf_grade, journal_alert=excluded.journal_alert,
                       stage=excluded.stage, final_score=excluded.final_score,
                       breakdown=excluded.breakdown""",
                [dict(r, run_id=run_id) for r in rows],
            )

    def replace_shortlist(self, run_id: str, rows: list[dict]) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM shortlist WHERE run_id=?", (run_id,))
            c.executemany(
                """INSERT INTO shortlist (run_id, pubpeer_id, major, stage1_score, stage2_score, final_score)
                   VALUES (:run_id,:pubpeer_id,:major,:stage1_score,:stage2_score,:final_score)""",
                [dict(r, run_id=run_id) for r in rows],
            )

    def scores_for_run(self, run_id: str) -> list[dict]:
        cur = self.conn.execute(
            "SELECT * FROM scores WHERE run_id=? ORDER BY final_score DESC", (run_id,))
        return _rows(cur)

    # ---- 爬虫表只读访问（不建表、不改动） ---------------------------------

    def all_captures(self) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM captures")
        return _rows(cur)

    def all_publications(self) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM publications")
        return _rows(cur)

    def all_comments(self) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM comments")
        return _rows(cur)

    def publications_for(self, pubpeer_ids: list[str]) -> dict[str, dict]:
        if not pubpeer_ids:
            return {}
        q = ",".join("?" * len(pubpeer_ids))
        cur = self.conn.execute(
            f"SELECT * FROM publications WHERE pubpeer_id IN ({q})", pubpeer_ids)
        return {r["pubpeer_id"]: r for r in _rows(cur)}

    def comments_for(self, pubpeer_ids: list[str]) -> dict[str, list[dict]]:
        if not pubpeer_ids:
            return {}
        q = ",".join("?" * len(pubpeer_ids))
        cur = self.conn.execute(
            f"SELECT * FROM comments WHERE pubpeer_id IN ({q}) ORDER BY accepted_at", pubpeer_ids)
        out: dict[str, list[dict]] = {}
        for r in _rows(cur):
            out.setdefault(r["pubpeer_id"], []).append(r)
        return out
