"""官网归档：把当期信息写进 `src/site/src/data/site.json`（公众号链接除外）。

设计：
- 派生规则单一锚点：`WEEK_START`（第 1 期数据收集起始日，默认 2026-08-03，与
  `run_issue.sh` / `llm.generate` / `agent.tools` 同算法）——
  窗口 = [start, start+6]，发布日 = start+9（周三）。
- 幂等且绝不覆盖人工数据：`issues[].wechatUrl` 是公众号链接的唯一来源，
  `wechat.url/label` 由它派生；派生不出链接且期号未提升时保持原值。
- 只写 `src/site/src/data/site.json`，不 commit、不 push。
"""

from .sync import (
    PENDING_LABEL_PREFIX,
    READ_LABEL_PREFIX,
    SITE_JSON_DEFAULT,
    WEEK_START_DEFAULT,
    SiteSyncError,
    issue_date,
    issue_window,
    main,
    plan_sync,
    run_selftest,
    sync_site_json,
    window_label,
)

__all__ = [
    "PENDING_LABEL_PREFIX",
    "READ_LABEL_PREFIX",
    "SITE_JSON_DEFAULT",
    "WEEK_START_DEFAULT",
    "SiteSyncError",
    "issue_date",
    "issue_window",
    "main",
    "plan_sync",
    "run_selftest",
    "sync_site_json",
    "window_label",
]
