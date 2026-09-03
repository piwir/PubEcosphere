"""中科院分区表 2025 分类：加载官方 CSV + 种子，按 ISSN / 期刊名分类。

匹配优先级：官方 CSV（ISSN→名）→ 种子（ISSN→名）→ 未匹配（显式标记）。
覆盖率报告输出每大类文章量，作为「定大类推送优先级」的依据。
"""
from __future__ import annotations

import csv
import html
import re
from dataclasses import dataclass, replace
from pathlib import Path

from .seed_cas import SEED_CAS

# 表头别名 → 规范化字段
_HEADER_ALIASES = {
    "journal": ["journal", "journal_name", "期刊名称", "期刊名", "刊名", "name"],
    "major": ["大类", "大类学科", "学科大类", "major"],
    "partition": ["分区", "大类分区", "partition"],
    "top": ["top", "top期刊", "是否top", "是否为top期刊"],
    "issn_combined": ["issn/eissn", "issn/e-issn", "issn_print/eissn", "issn/e issn"],
    "issn_print": ["issn", "print_issn", "印刷版issn", "印刷issn"],
    "issn_e": ["eissn", "issn_e", "issn(electronic)", "电子版issn", "电子issn"],
}


def _norm_header(h: str) -> str:
    return re.sub(r"\s+", "", (h or "").lower())


def _pick_header(headers: list[str], aliases: list[str]) -> str | None:
    for alias in aliases:
        key = _norm_header(alias)
        for h in headers:
            if _norm_header(h) == key:
                return h
    return None


def issn_key(issn: str | None) -> str:
    return re.sub(r"\D", "", issn or "")[:8]


def name_key(name: str | None) -> str:
    s = html.unescape(name or "").lower()
    s = re.sub(r"\(.*?\)", "", s)          # 去括号/后缀
    s = re.sub(r"\W+", " ", s).strip()
    return s


def _parse_partition(v) -> int:
    if v is None:
        return 0
    m = re.search(r"\d+", str(v))
    return int(m.group()) if m else 0


def _parse_bool(v) -> bool:
    if v is None:
        return False
    return str(v).strip().lower() in ("是", "1", "true", "yes", "y", "top", "t")


_CJK = re.compile(r"[一-鿿][一-鿿：·（）()]*")


def minor_names(minor: str) -> list[str]:
    """从 minor 字符串提取全部小类的中文名（如 ['细胞生物学', '肿瘤学']）。"""
    out: list[str] = []
    for seg in (minor or "").split(";"):
        runs = _CJK.findall(seg.strip())
        if runs:
            out.append(runs[-1])
    return out


def minor_name(minor: str) -> str:
    """第一条小类的中文名（如 '细胞生物学'）。"""
    names = minor_names(minor)
    return names[0] if names else ""


@dataclass
class Classification:
    major: str = ""
    minor: str = ""
    minor_partition: int = 0
    partition: int = 0
    top: bool = False
    source: str = "unmatched"   # official | seed | unmatched
    method: str = ""            # issn | name | name_contains


class CasIndex:
    """分类索引：ISSN 键 + 期刊名键 -> Classification。"""

    def __init__(self) -> None:
        self._by_issn: dict[str, Classification] = {}
        self._by_name: dict[str, Classification] = {}
        self.rows: int = 0
        self.dup_issn: int = 0

    # ---- 构建 -------------------------------------------------------------

    @classmethod
    def build(cls, csv_path: str | None = None, seed_only: bool = False) -> "CasIndex":
        idx = cls()
        if not seed_only and csv_path and Path(csv_path).exists():
            idx.load_csv(csv_path)
        idx.load_seed()
        return idx

    def _add(self, c: Classification, issn: str | None, name: str | None) -> None:
        if issn:
            k = issn_key(issn)
            if k and k not in self._by_issn:
                self._by_issn[k] = c
            elif k and k in self._by_issn:
                self.dup_issn += 1
        if name:
            k = name_key(name)
            if k and k not in self._by_name:
                self._by_name[k] = c

    def load_seed(self) -> None:
        for name, issn, major, part, top, src in SEED_CAS:
            self._add(Classification(major=major, partition=part, top=bool(top), source=src),
                      issn, name)

    def load_csv(self, path: str | Path) -> None:
        with open(path, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            headers = next(reader)
            col = {canon: _pick_header(headers, aliases)
                   for canon, aliases in _HEADER_ALIASES.items()}
        if not col["journal"] or not col["major"]:
            raise ValueError(f"CAS CSV 缺少必需列（journal/大类）: {path}")
        with open(path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                major = (row.get(col["major"]) or "").strip()
                if not major:
                    continue
                c = Classification(
                    major=major,
                    minor=self._collect_minor(row, headers),
                    minor_partition=self._extract_minor_partition(row, headers),
                    partition=_parse_partition(row.get(col["partition"])),
                    top=_parse_bool(row.get(col["top"])),
                    source="official",
                )
                name = row.get(col["journal"])
                issn = self._extract_issn(row, col)
                self.rows += 1
                self._add(c, issn, name)

    @staticmethod
    def _extract_issn(row: dict, col: dict) -> str | None:
        combined = col["issn_combined"]
        if combined:
            v = (row.get(combined) or "").strip()
            if v:
                return v.split("/")[0].strip()
        for key in (col["issn_print"], col["issn_e"]):
            if key:
                v = (row.get(key) or "").strip()
                if v:
                    return v
        return None

    @staticmethod
    def _is_minor_name_col(nh: str) -> bool:
        # 小类 / 小类1..小类6 的名字列；排除 小类分区 / 小类1分区 等分区列
        return nh.startswith("小类") and not re.match(r"^小类\d*分区", nh)

    @staticmethod
    def _collect_minor(row: dict, headers: list[str]) -> str:
        # 收集 小类1..小类6 名字非空值，';' 连接（不含分区列）
        parts = []
        for h in headers:
            if CasIndex._is_minor_name_col(_norm_header(h)):
                v = (row.get(h) or "").strip()
                if v:
                    parts.append(v)
        return "; ".join(parts)

    @staticmethod
    def _extract_minor_partition(row: dict, headers: list[str]) -> int:
        # 取第一条 小类N分区 的值（如 "2 [33/203]" → 2）
        for h in headers:
            if re.match(r"^小类\d*分区", _norm_header(h)):
                return _parse_partition(row.get(h))
        return 0

    # ---- 分类 -------------------------------------------------------------

    def classify(self, issn: str | None, journal: str | None) -> Classification:
        if issn:
            k = issn_key(issn)
            if k and k in self._by_issn:
                return replace(self._by_issn[k], method="issn")
        if journal:
            k = name_key(journal)
            if k in self._by_name:
                return replace(self._by_name[k], method="name")
            for nk, c in self._by_name.items():
                if k and (k in nk or nk in k):
                    return replace(c, method="name_contains")
        return Classification()

    # ---- 覆盖率 -----------------------------------------------------------

    def coverage(self, captures: list[dict]) -> list[dict]:
        from collections import defaultdict
        by_journal: dict[str, dict] = defaultdict(lambda: {"n": 0, "issns": set()})
        for cap in captures:
            j = cap.get("journal") or ""
            by_journal[j]["n"] += 1
            if cap.get("issn"):
                by_journal[j]["issns"].add(cap["issn"])

        out = []
        for journal, rec in by_journal.items():
            issn = next(iter(rec["issns"])) if rec["issns"] else None
            cl = self.classify(issn, journal)
            reason = ""
            if cl.source == "unmatched":
                if not issn:
                    reason = "no_issn"
                elif cl.method == "name_contains" or journal:
                    reason = "name_unmatched"
                else:
                    reason = "issn_unmatched"
            out.append({
                "journal": journal,
                "n": rec["n"],
                "issn": issn or "",
                "status": cl.source,
                "method": cl.method,
                "major": cl.major,
                "minor_name": minor_name(cl.minor),
                "reason": reason,
            })
        return sorted(out, key=lambda r: (-r["n"], r["journal"]))
