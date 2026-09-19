"""JCR 影响因子 2025：ISSN/EISSN → IF(2025)。

作为「期刊影响因子」维度的数据源；无 IF（ESCI/新收录）时上层回退 CAS 分区 / CCF 等级。
"""
from __future__ import annotations

import csv
import html
import re
from pathlib import Path

from .cas import fuzzy_name_match, issn_key, name_key


def _find_if_col(headers: list[str]) -> str | None:
    for h in headers:
        nh = re.sub(r"\s+", "", (h or "").lower())
        if nh.startswith("if(") or nh == "if" or "impactfactor" in nh or "影响因子" in nh:
            return h
    return None


class JcrIndex:
    def __init__(self) -> None:
        self._by_issn: dict[str, float] = {}
        self._by_name: dict[str, float] = {}
        self.rows: int = 0

    @classmethod
    def build(cls, csv_path: str | None = None) -> "JcrIndex":
        idx = cls()
        if csv_path and Path(csv_path).exists():
            idx.load_csv(csv_path)
        return idx

    def load_csv(self, path: str | Path) -> None:
        with open(path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return
            if_col = _find_if_col(reader.fieldnames)
            if not if_col:
                raise ValueError(f"JCR CSV 缺少 IF 列: {path}")
            for row in reader:
                try:
                    ifv = float((row.get(if_col) or "").strip())
                except (TypeError, ValueError):
                    continue
                journal = row.get("Journal") or row.get("journal")
                for k in ("ISSN", "EISSN"):
                    if row.get(k):
                        key = issn_key(row[k])
                        if key and key not in self._by_issn:
                            self._by_issn[key] = ifv
                if journal:
                    nk = name_key(journal)
                    if nk and nk not in self._by_name:
                        self._by_name[nk] = ifv
                self.rows += 1

    def lookup(self, issn: str | None, journal: str | None) -> float | None:
        if issn:
            v = self._by_issn.get(issn_key(issn))
            if v is not None:
                return v
        if journal:
            nk = name_key(journal)
            if nk in self._by_name:
                return self._by_name[nk]
            # 兑底模糊匹配：见 cas.fuzzy_name_match（长度比阈值 + 最长 key，防短刊名撞车）
            _, v = fuzzy_name_match(nk, self._by_name)
            if v is not None:
                return v
        return None
