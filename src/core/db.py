"""SQLite 连接与事务：两个 Store 共享的连接参数，**不共享表结构**。

`crawler/store.py`（captures/publications/comments）与 `scoring/store.py`
（scores/shortlist/published/doi_resolution/v3_feedback）对同一个库文件各建各的表，
但连接参数一改就得改两处、且曾因默认 5s 锁等待在 cron 里天天「database is locked」。
这里只收敛「怎么连、怎么开事务」：

    connect(path)      30s 超时 + busy_timeout=30000 + 建父目录（写库用）
    connect_ro(path)   只读打开，库/表缺失时干净报 MissingTable（读库用，不建文件）
    require_tables()   查询前确认表存在，缺失抛 MissingTable
    tx(conn)           上下文管理器：正常 commit、异常 rollback

**为什么要有 `connect_ro`/`MissingTable`**：agent 会先跑 `status`/`coverage` 探路，
旧实现直接 `sqlite3.connect` 会「静默创建空库 + 抛 sqlite3.OperationalError traceback」，
agent 只能看到一个没有语义的堆栈。现在读路径统一给一句人话。

表 DDL、迁移、查询一律留在各自的 Store 里。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

TIMEOUT_SECONDS = 30.0


class MissingTable(RuntimeError):
    """库里没有流水线需要的表（通常是库不存在/还没跑过 capture）。"""

    def __init__(self, table: str | None = None, path: str | None = None):
        self.table = table
        self.path = path
        where = f"（库里没有 `{table}` 表）" if table else ""
        whose = f"：{path}" if path else ""
        super().__init__(
            f"数据库不可用{where}{whose}。"
            "先确认 --db 指向正确的库，或先跑一次 `python -m crawler.crawl --db <db> capture` 建库取数。")


def connect(path: str | Path) -> sqlite3.Connection:
    """打开（必要时创建父目录）SQLite 连接；并发写场景给足 30s 锁等待。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=TIMEOUT_SECONDS)
    conn.execute(f"PRAGMA busy_timeout={int(TIMEOUT_SECONDS * 1000)}")
    return conn


def connect_ro(path: str | Path) -> sqlite3.Connection:
    """只读打开**已存在**的库；文件不存在抛 MissingTable，绝不静默建空库。

    供 status/coverage 这类「探路」命令用：没有库是合法状态，应如实报告而不是
    创建出一个空库让人误以为有数据。
    """
    p = Path(path)
    if not p.exists():
        raise MissingTable(path=str(p))
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=TIMEOUT_SECONDS)
    conn.execute(f"PRAGMA busy_timeout={int(TIMEOUT_SECONDS * 1000)}")
    return conn


def require_tables(conn: sqlite3.Connection, *tables: str, path: str | None = None) -> None:
    """确认这些表都存在；缺表时抛 MissingTable（避免上层满地 traceback）。"""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in tables:
        if t not in have:
            raise MissingTable(t, path)


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """事务上下文：正常结束 commit，异常 rollback 后原样抛出。"""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
