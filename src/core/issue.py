"""期号 ↔ 日期窗口：**唯一锚点**，所有期号派生都走这里。

锚点：第 1 期素材收集起始日 `WEEK_START`（默认 2026-08-03，可由环境变量覆盖）。
每期 7 天，全部派生自 `issue_start()`：

    素材窗口（起含止含）  [start, start+6]        → 官网 window 标签
    打分窗口（起含止不含） [start, start+7)        → rank --window-dates
    官网发布日             start + 9（周三）       → site.json issueDate
    导语标签               `M.DD–M.DD`（start..start+6）→ 写稿头部「数据收集：」

改动这里 = 改全流水线（run_issue.sh / rank / generate / sitejson / agent 工具），
**不要在任何调用点再手写 `(期号-1)*7`**。
"""
from __future__ import annotations

import os
from datetime import date, timedelta

WEEK_START_DEFAULT = "2026-08-03"
WEEK_START_ENV = "WEEK_START"
ISSUE_DAYS = 7            # 每期素材窗口长度
PUBLISH_OFFSET_DAYS = 9   # 素材窗口起始日 → 公众号发布日（周三）


class IssueError(ValueError):
    """期号或 WEEK_START 非法。调用方可按需转成自己的错误类型。"""


def week_start_base(week_start: str | date | None = None) -> date:
    """解析锚点：显式参数 > 环境变量 WEEK_START > 默认值。非法则抛 IssueError。"""
    if isinstance(week_start, date):
        return week_start
    raw = week_start or os.environ.get(WEEK_START_ENV, WEEK_START_DEFAULT)
    try:
        return date.fromisoformat(str(raw))
    except ValueError as exc:
        raise IssueError(f"{WEEK_START_ENV} 不是合法日期（应为 ISO yyyy-mm-dd）：{raw!r}") from exc


def is_positive_issue(issue: object) -> bool:
    """正整数期号判定（`-1` 测试期、`0`、空串均 False）。"""
    raw = str(issue).strip()
    return raw.isdigit() and int(raw) > 0


def require_positive_issue(issue: object) -> int:
    """校验并转换正整数期号；非法抛 IssueError。"""
    if not is_positive_issue(issue):
        raise IssueError(f"期号必须是正整数（收到 {issue!r}；-1 测试期无日期窗口）")
    return int(str(issue).strip())


def issue_start(issue: int, week_start: str | date | None = None) -> date:
    """期号 → 素材窗口起始日（start = 锚点 + (期号-1)*7）。"""
    n = require_positive_issue(issue)
    return week_start_base(week_start) + timedelta(days=(n - 1) * ISSUE_DAYS)


def issue_window(issue: int, week_start: str | date | None = None) -> tuple[date, date]:
    """期号 → 素材窗口 [start, start+6]（起含止含；官网 `window` 与导语标签用）。"""
    start = issue_start(issue, week_start)
    return start, start + timedelta(days=ISSUE_DAYS - 1)


def scoring_window(issue: int, week_start: str | date | None = None) -> tuple[str, str]:
    """期号 → 打分绝对窗口 [start, start+7) 的 ISO 字符串（rank/revisit `--window-dates`）。"""
    start = issue_start(issue, week_start)
    return start.isoformat(), (start + timedelta(days=ISSUE_DAYS)).isoformat()


def publish_date(issue: int, week_start: str | date | None = None) -> date:
    """期号 → 公众号发布日（素材窗口起始 + 9 天 = 周三；写进 site.json issueDate）。"""
    return issue_start(issue, week_start) + timedelta(days=PUBLISH_OFFSET_DAYS)


def window_label(issue: int, week_start: str | date | None = None) -> str:
    """期号 → 官网窗口标签 `YYYY-MM-DD – YYYY-MM-DD`（起含止含）。"""
    start, end = issue_window(issue, week_start)
    return f"{start.isoformat()} – {end.isoformat()}"


def date_range_label(issue: int, week_start: str | date | None = None) -> str:
    """期号 → 导语标签 `M.DD–M.DD`（如 `8.03–8.09`；起含止含）。"""
    start, end = issue_window(issue, week_start)
    return f"{start.month}.{start.day:02d}–{end.month}.{end.day:02d}"


def material_window_label(issue: int, week_start: str | date | None = None) -> str:
    """期号 → 导语里的 `（素材收集 M.DD–M.DD）` 内容（不带括号）。"""
    return date_range_label(issue, week_start)
