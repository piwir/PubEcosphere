"""期刊影响力展示：**唯一格式化口径**。

卡片 / 报告 / manifest 三处都要显示「IF 15.6」或「2区·Top」或「CCF A」，
此前 `scoring/report.py` 与 `scoring/issue.py` 各写一份（一个用 `+` 拼接、一个用
f-string，输出等价但易漂）。统一走这里：

    impact_display(record) → "IF 15.6" | "2区·Top" | "CCF A" | "-"

`record` 是任何带 `impact_factor / partition / top / ccf_grade` 键的映射
（scores 行、journal info、manifest pick 都满足）。
"""
from __future__ import annotations

from typing import Mapping


def impact_display(record: Mapping | dict | None) -> str:
    """按 IF → 中科院分区/ Top → CCF 等级 → `-` 的优先级取展示串。"""
    r = record or {}
    if r.get("impact_factor"):
        return f"IF {r['impact_factor']:.1f}"
    if r.get("partition"):
        return f"{r['partition']}区" + ("·Top" if r.get("top") else "")
    if r.get("ccf_grade"):
        return f"CCF {r['ccf_grade']}"
    return "-"
