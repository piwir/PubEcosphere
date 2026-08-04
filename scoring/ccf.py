"""CCF 推荐目录 + 国际期刊预警名单。

- CCF2026 / CCFT2025：会议/期刊兜底（CAS/JCR 匹配失败时），A/B/C 与 T1/T2/T3。
- GJQKYJMD2025：期刊预警名单，journal_alert 维度 + 报告 ⚠️ 展示。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .cas import name_key

_GRADE_RANK = {"A类": "A", "B类": "B", "C类": "C", "T1": "T1", "T2": "T2", "T3": "T3"}


def _find_col(headers: list[str], aliases: list[str]) -> str | None:
    for a in aliases:
        for h in headers:
            if (h or "").strip().lower().startswith(a.lower()):
                return h
    return None


@dataclass
class CcfInfo:
    grade: str = ""      # A/B/C 或 T1/T2/T3
    kind: str = ""       # journal | conference
    alert: str = ""      # 预警原因（如"论文工厂"）


class CcfIndex:
    def __init__(self) -> None:
        self._by_name: dict[str, CcfInfo] = {}
        self._alerts: dict[str, str] = {}     # name_key -> 预警原因

    # ---- 构建 -------------------------------------------------------------

    @classmethod
    def build(cls, ccf_csv: str | None = None, ccft_csv: str | None = None,
              alert_csv: str | None = None) -> "CcfIndex":
        idx = cls()
        if ccf_csv and Path(ccf_csv).exists():
            idx.load_ccf(ccf_csv)
        if ccft_csv and Path(ccft_csv).exists():
            idx.load_ccft(ccft_csv)
        if alert_csv and Path(alert_csv).exists():
            idx.load_alerts(alert_csv)
        return idx

    def load_ccf(self, path: str | Path) -> None:
        with open(path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            journal_col = _find_col(reader.fieldnames or [], ["Journal", "刊物名称"])
            grade_col = _find_col(reader.fieldnames or [], ["CCF推荐类型", "推荐类型"])
            type_col = _find_col(reader.fieldnames or [], ["CCF推荐类别"])
            if not journal_col or not grade_col:
                return
            for row in reader:
                name = (row.get(journal_col) or "").strip()
                grade_raw = (row.get(grade_col) or "").strip()
                grade = _GRADE_RANK.get(grade_raw, "")
                if not name or not grade:
                    continue
                kind_raw = (row.get(type_col) or "") if type_col else ""
                kind = "conference" if "会议" in kind_raw else "journal"
                nk = name_key(name)
                if nk and nk not in self._by_name:
                    self._by_name[nk] = CcfInfo(grade=grade, kind=kind)

    def load_ccft(self, path: str | Path) -> None:
        with open(path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            journal_col = _find_col(reader.fieldnames or [], ["Journal"])
            t_col = _find_col(reader.fieldnames or [], ["T分区", "T"])
            if not journal_col or not t_col:
                return
            for row in reader:
                name = (row.get(journal_col) or "").strip()
                grade = (row.get(t_col) or "").strip()
                if not name or grade not in ("T1", "T2", "T3"):
                    continue
                nk = name_key(name)
                if nk and nk not in self._by_name:
                    self._by_name[nk] = CcfInfo(grade=grade, kind="journal")

    def load_alerts(self, path: str | Path) -> None:
        with open(path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            journal_col = _find_col(reader.fieldnames or [], ["Journal", "期刊"])
            reason_col = _find_col(reader.fieldnames or [], ["预警原因", "原因"])
            if not journal_col:
                return
            for row in reader:
                name = (row.get(journal_col) or "").strip()
                if not name:
                    continue
                reason = (row.get(reason_col) or "").strip() if reason_col else ""
                nk = name_key(name)
                if nk and nk not in self._alerts:
                    self._alerts[nk] = reason

    # ---- 查询 -------------------------------------------------------------

    def lookup(self, journal: str | None) -> CcfInfo:
        if not journal:
            return CcfInfo()
        nk = name_key(journal)
        info = self._by_name.get(nk) or CcfInfo()
        alert = self._alerts.get(nk, "")
        return CcfInfo(grade=info.grade, kind=info.kind, alert=alert)

    def is_alert(self, journal: str | None) -> str:
        return self._alerts.get(name_key(journal), "") if journal else ""
