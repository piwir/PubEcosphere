"""官网归档：把当期信息写进 `src/site/src/data/site.json`（公众号链接除外）。

设计：
- 周报（sync）：派生规则单一锚点 `WEEK_START`（第 1 期数据收集起始日，默认 2026-08-03，与
  `run_issue.sh` / `llm.generate` / `agent.tools` 同算法）——
  窗口 = [start, start+6]，发布日 = start+9（周三）；
  `issues[].wechatUrl` 是公众号链接的唯一人工填写处，`wechat.url/label` 每次 sync 都由它重算。
- AI4S 推文（sync-ai4s）：从 PubAI4S 产物目录派生条目写进 `ai4s.posts`，
  只动 `ai4s`（周报字段一概不动）；`ai4s.posts[].url` 是对应的唯一人工填写处。
- 只写 `src/site/src/data/site.json`，不 commit、不 push。
"""

from .sync import (
    AI4S_DIR_DEFAULT,
    AI4S_GITHUB_DEFAULT,
    AI4S_POST_KEYS,
    READ_LABEL_PREFIX,
    SITE_JSON_DEFAULT,
    WEEK_START_DEFAULT,
    SiteSyncError,
    derive_ai4s_meta,
    issue_date,
    issue_window,
    main,
    plan_sync,
    plan_sync_ai4s,
    run_selftest,
    sync_ai4s_post,
    sync_site_json,
    window_label,
)

__all__ = [
    "AI4S_DIR_DEFAULT",
    "AI4S_GITHUB_DEFAULT",
    "AI4S_POST_KEYS",
    "READ_LABEL_PREFIX",
    "SITE_JSON_DEFAULT",
    "WEEK_START_DEFAULT",
    "SiteSyncError",
    "derive_ai4s_meta",
    "issue_date",
    "issue_window",
    "main",
    "plan_sync",
    "plan_sync_ai4s",
    "run_selftest",
    "sync_ai4s_post",
    "sync_site_json",
    "window_label",
]
