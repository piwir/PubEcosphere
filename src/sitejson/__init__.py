"""官网归档：把当期信息写进 `src/site/src/data/site.json`（公众号链接除外）。

设计：
- 派生规则单一锚点：`WEEK_START`（第 1 期数据收集起始日，默认 2026-08-03，与
  `run_issue.sh` / `llm.generate` / `agent.tools` 同算法）——
  窗口 = [start, start+6]，发布日 = start+9（周三）。
- 幂等且完全派生：`issues[].wechatUrl` 是公众号链接的唯一人工填写处，
  `wechat.url/label` 每次 sync 都由它重算（空 → url 空 + 默认「阅读第 N 期周报」占位）。
- 只写 `src/site/src/data/site.json`，不 commit、不 push。
"""

from .sync import (
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
