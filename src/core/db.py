"""SQLite 连接与事务：两个 Store 共享的连接参数，**不共享表结构**。

`crawler/store.py`（captures/publications/comments）与 `scoring/store.py`
（scores/shortlist/published/doi_resolution/v3_feedback）对同一个库文件各建各的表，
但连接参数一改就得改两处、且曾因默认 5s 锁等待在 cron 里天天「database is locked」。
这里只收敛「怎么连、怎么开事务」：

    connect(path)   30s 超时 + busy_timeout=30000 + 建父目录
    tx(conn)        上下文管理器：正常 commit、异常 rollback

表 DDL、迁移、查询一律留在各自的 Store 里。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

TIMEOUT_SECONDS = 30.0


def connect(path: str | Path) -> sqlite3.Connection:
    """打开（必要时创建父目录）SQLite 连接；并发写场景给足 30s 锁等待。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=TIMEOUT_SECONDS)
    conn.execute(f"PRAGMA busy_timeout={int(TIMEOUT_SECONDS * 1000)}")
    return conn


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """事务上下文：正常结束 commit，异常 rollback 后原样抛出。"""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
