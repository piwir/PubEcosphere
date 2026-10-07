"""官网归档核心：`src/site/src/data/site.json` 的确定性、幂等更新。

用法：
    python -m sitejson sync --issue 6              # 把第 6 期写进 site.json（幂等）
    python -m sitejson sync --issue 6 --dry-run    # 只看将发生的变化，不写盘
    python -m sitejson sync-ai4s --dir output/PubAI4S/<owner>-<repo>   # AI4S 推文归档（幂等）
    python -m sitejson --selftest                  # 离线自测（临时目录 fixture）

派生规则（与已发布第 1–5 期实测值一致）：
    窗口   [WEEK_START+(N-1)*7, +6 天]      → window: "YYYY-MM-DD – YYYY-MM-DD"
    发布日 WEEK_START+(N-1)*7 + 9 天        → issueDate（周三）

写入规则（幂等、绝不覆盖人工数据）：
    1. issues[]：已有第 N 期条目则补 date（summary 仅在为空时补）；否则头部插入
       {issue, date, wechatUrl: "", summary}；
    2. currentIssue / issueDate / window 写成本期派生值；
    3. wechat（首页 CTA）：**完全由 issues[N].wechatUrl 派生**（唯一人工填写处，每期只填一次）：
       url = issues[N].wechatUrl（未发布则为空，首页显示不可点的同一文案）；
       label 恒为默认文案 "阅读第 N 期周报"；即：新一期生成后 wechat 与 issues 始终一致。
    4. tagline / github / ai4s 一概不动。

AI4S 推文归档（sync-ai4s，与上互不影响）：
    1. 只动 ai4s.posts（issues / currentIssue / window / wechat / tagline / github 一概不动）；
    2. 条目从 PubAI4S 产物目录派生：repo/name/summary 取自 post.md 与 inputs/meta.txt，
       date 默认取 post.md 修改日期（= 生成日，发布日不同时传 --date）；
    3. ai4s.posts[].url 是公众号链接的唯一人工填写处（与 issues[].wechatUrl 同理）：
       已有非空链接不会被空值覆盖；发布后传 --url 或用编辑器补上再重跑即可；
    4. 按 repo 去重：同一仓库重复跑只更新 date/name/summary，不新增条目。

退出码：成功 0；期号非法 / JSON 非法 / 文件缺失 → 1。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from repopath import REPO_ROOT

# 第 1 期数据收集起始日：与 run_issue.sh / llm.generate / agent.tools 同一锚点。
WEEK_START_DEFAULT = "2026-08-03"
SITE_JSON_DEFAULT = REPO_ROOT / "src" / "site" / "src" / "data" / "site.json"

SUMMARY_TEMPLATE = "PubPeer 周报 · issue {n}"
# 首页 CTA 文案：与是否已发布无关，恒为此默认值（未发布时 url 为空 → 渲染成不可点的 span）。
READ_LABEL_PREFIX = "阅读第 {n} 期周报"


class SiteSyncError(Exception):
    """可预期的失败（期号非法 / 文件缺失 / JSON 非法）——CLI 捕获后干净退出 1。"""


# ---- 派生 ----------------------------------------------------------------

def _week_start(week_start: str | None = None) -> date:
    raw = week_start or os.environ.get("WEEK_START", WEEK_START_DEFAULT)
    try:
        return date.fromisoformat(str(raw))
    except ValueError as exc:
        raise SiteSyncError(f"WEEK_START 不是合法日期（应为 ISO yyyy-mm-dd）：{raw!r}") from exc


def _positive_issue(issue: object) -> int:
    raw = str(issue)
    if not (raw.isdigit() and int(raw) > 0):
        raise SiteSyncError(f"sitejson 只写正整数期号（收到 {raw!r}；-1 测试期不写站点数据）")
    return int(raw)


def issue_window(issue: int, week_start: str | None = None) -> tuple[date, date]:
    """期号 → 素材窗口 [start, start+6 天]（起含止含）。"""
    start = _week_start(week_start) + timedelta(days=(_positive_issue(issue) - 1) * 7)
    return start, start + timedelta(days=6)


def issue_date(issue: int, week_start: str | None = None) -> date:
    """期号 → 官网发布日（窗口起始 + 9 天 = 周三）。"""
    start, _ = issue_window(issue, week_start)
    return start + timedelta(days=9)


def window_label(issue: int, week_start: str | None = None) -> str:
    start, end = issue_window(issue, week_start)
    return f"{start.isoformat()} – {end.isoformat()}"


# ---- 读写 ----------------------------------------------------------------

def _load(path: Path) -> dict:
    if not path.exists():
        raise SiteSyncError(f"site.json 不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SiteSyncError(f"site.json 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise SiteSyncError("site.json 顶层不是对象")
    return data


def _dump(data: dict) -> str:
    """与仓库现有格式一致：2 空格缩进 + 末尾换行（key 顺序按加载顺序保留）。"""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".site.json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


# ---- 计划（不写盘）--------------------------------------------------------

def plan_sync(data: dict, issue: int, *, week_start: str | None = None,
              date_override: str | None = None, window_override: str | None = None,
              summary: str | None = None) -> tuple[dict, list[str]]:
    """在内存里算出新 site.json 与人类可读的变化清单（不改入参、不写盘）。"""
    n = _positive_issue(issue)
    out = json.loads(json.dumps(data))  # 深拷贝，保护调用方
    changes: list[str] = []

    pub_date = str(date_override) if date_override else issue_date(n, week_start).isoformat()
    window = str(window_override) if window_override else window_label(n, week_start)
    want_summary = summary or SUMMARY_TEMPLATE.format(n=n)

    issues = out.get("issues")
    if not isinstance(issues, list):
        raise SiteSyncError("site.json 的 issues 不是数组")
    entry = next((it for it in issues if isinstance(it, dict) and it.get("issue") == n), None)
    if entry is None:
        entry = {"issue": n, "date": pub_date, "wechatUrl": "", "summary": want_summary}
        issues.insert(0, entry)
        changes.append(f"+ issues[] 头部新增第 {n} 期归档条目（summary={want_summary!r}）")
    else:
        if entry.get("date") != pub_date:
            changes.append(f"~ issues[{n}].date: {entry.get('date')!r} → {pub_date!r}")
            entry["date"] = pub_date
        if not entry.get("summary"):
            changes.append(f"~ issues[{n}].summary: {entry.get('summary')!r} → {want_summary!r}")
            entry["summary"] = want_summary
        else:
            want_summary = entry["summary"]

    for key, val in (("currentIssue", n), ("issueDate", pub_date), ("window", window)):
        if out.get(key) != val:
            changes.append(f"~ {key}: {out.get(key)!r} → {val!r}")
            out[key] = val

    wechat = out.get("wechat")
    if not isinstance(wechat, dict):
        raise SiteSyncError("site.json 的 wechat 不是对象")
    link = str(entry.get("wechatUrl") or "").strip()
    # wechat 永远由 issues[N].wechatUrl 派生：填链接只在 issues[] 一处；label 恒为默认文案。
    label = READ_LABEL_PREFIX.format(n=n)
    if link:
        if wechat.get("url") != link:
            changes.append(f"~ wechat.url: {wechat.get('url')!r} → {link!r}（取自 issues[{n}].wechatUrl）")
    elif wechat.get("url"):
        changes.append(f"~ wechat.url: {wechat.get('url')!r} → ''（第 {n} 期链接未发布）")
    if wechat.get("label") != label:
        changes.append(f"~ wechat.label: {wechat.get('label')!r} → {label!r}")
    wechat["url"], wechat["label"] = link, label

    return out, changes


def sync_site_json(site_json_path: str | Path | None = None, issue: int = 0, *,
                   week_start: str | None = None, date_override: str | None = None,
                   window_override: str | None = None, summary: str | None = None,
                   dry_run: bool = False, quiet: bool = False) -> tuple[dict, list[str]]:
    """读 site.json → 计划 → （非 dry-run 时）原子写回。返回 (新数据, 变化清单)。"""
    path = Path(site_json_path or SITE_JSON_DEFAULT)
    data = _load(path)
    new, changes = plan_sync(data, issue, week_start=week_start,
                             date_override=date_override, window_override=window_override,
                             summary=summary)
    manifest = REPO_ROOT / "output" / "issue" / str(_positive_issue(issue)) / "manifest.json"
    if not manifest.exists() and not quiet:
        print(f"  [告警] 未找到本期素材清单 {manifest}（仍按期号写入归档条目）", file=sys.stderr)
    if not dry_run:
        _write_atomic(path, _dump(new))
    return new, changes


# ---- AI4S 推文归档（sync-ai4s）-------------------------------------------

AI4S_GITHUB_DEFAULT = "https://github.com/piwir/PubAI4S"
AI4S_DIR_DEFAULT = REPO_ROOT / "output" / "PubAI4S"
AI4S_POST_KEYS = ("no", "repo", "name", "date", "summary", "url")
# 结尾固定块 `> GitHub：[github.com/o/n](...)`：两份提示词约定的唯一出处，避开正文里的第三方链接
_ENDING_REPO_RE = re.compile(r"^>\s*GitHub[：:]\s*\[([^\]]+)\]", re.M)
_META_REPO_RE = re.compile(r"仓库链接[：:]\s*(https?://github\.com/[^\s)]+)")
_MD_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.M)


def _ai4s_dir(raw: str | Path) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else REPO_ROOT / p


def _norm_repo(raw: str) -> str:
    """把各种写法的仓库地址归一为 owner/repo（去 scheme、github.com/ 前缀、.git、首尾斜杠）。"""
    repo = raw.strip()
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        repo = repo.removeprefix(prefix)
    return repo.removesuffix(".git").strip("/")


def derive_ai4s_meta(post_dir: Path) -> tuple[dict, list[str]]:
    """从 PubAI4S 产物目录派生条目字段（repo/name/summary/date）——只读 post.md 与 inputs/meta.txt。"""
    post_md = post_dir / "post.md"
    if not post_md.exists():
        raise SiteSyncError(f"找不到推文正文：{post_md}（先跑 pubai4s fetch 并完成写稿/渲染）")
    text = post_md.read_text(encoding="utf-8")

    repo = ""
    m = _ENDING_REPO_RE.search(text)  # 结尾固定块（避免误取正文里的 AlphaFold3 / uv 等第三方链接）
    if m:
        repo = _norm_repo(m.group(1))
    if not repo:
        meta = post_dir / "inputs" / "meta.txt"
        if meta.exists():
            m2 = _META_REPO_RE.search(meta.read_text(encoding="utf-8"))
            if m2:
                repo = _norm_repo(m2.group(1))
    if "/" not in repo:
        raise SiteSyncError(f"无法从 {post_md} 推导仓库（需结尾 `> GitHub：[owner/repo](...)` "
                            f"或 inputs/meta.txt 的仓库链接）")

    title = ""
    mt = _MD_TITLE_RE.search(text)
    if mt:
        title = mt.group(1).strip()
    name = title.split(" · ")[0].strip() if title else repo.split("/")[-1]
    warnings: list[str] = []
    if not title:
        warnings.append("post.md 缺主标题 `# 项目名 · 钩子`：name 退化为仓库名、summary 留空")
    return {"repo": repo, "name": name, "summary": title,
            "date": date.fromtimestamp(post_md.stat().st_mtime).isoformat()}, warnings


def plan_sync_ai4s(data: dict, *, repo: str, name: str, summary: str, pub_date: str,
                   url: str | None = None, no: int | None = None,
                   date_explicit: bool = False) -> tuple[dict, list[str]]:
    """在内存里算出新 site.json 与变化清单：只动 ai4s.posts，按 repo 去重。

    不覆盖人工值：`url` 只在显式传值时写；`date` 派生自 post.md 修改时间（非确定），
    已有条目仅在显式 `--date`（date_explicit）时更新，避免「发布后补链接再跑一次」把发布日改回生成日。
    """
    out = json.loads(json.dumps(data))
    changes: list[str] = []

    ai4s = out.get("ai4s")
    if not isinstance(ai4s, dict):
        ai4s = {"github": AI4S_GITHUB_DEFAULT, "posts": []}
        out["ai4s"] = ai4s
        changes.append(f"+ ai4s 缺失 → 新建（github={AI4S_GITHUB_DEFAULT}）")
    posts = ai4s.get("posts")
    if not isinstance(posts, list):
        raise SiteSyncError("site.json 的 ai4s.posts 不是数组")

    entry = next((p for p in posts if isinstance(p, dict) and p.get("repo") == repo), None)
    if entry is None:
        existing = [p.get("no") for p in posts
                    if isinstance(p, dict) and isinstance(p.get("no"), int)]
        n = no if no is not None else (max(existing) + 1 if existing else 1)
        entry = {"no": n, "repo": repo, "name": name, "date": pub_date,
                 "summary": summary, "url": (url or "").strip()}
        posts.insert(0, entry)  # 与 site/README 的人工规则一致：头部加一条
        changes.append(f"+ ai4s.posts 头部新增 NO.{n} {repo}（date={pub_date}）")
    else:
        for key, val in (("name", name), ("summary", summary)):
            if val and entry.get(key) != val:
                changes.append(f"~ ai4s.posts[{repo}].{key}: {entry.get(key)!r} → {val!r}")
                entry[key] = val
        # date 派生值非确定：仅显式 --date 或条目尚无日期时更新（对齐 url 的人工值保护）
        if pub_date and (date_explicit or not entry.get("date")) and entry.get("date") != pub_date:
            changes.append(f"~ ai4s.posts[{repo}].date: {entry.get('date')!r} → {pub_date!r}")
            entry["date"] = pub_date
        # url 只在显式传值时写：它是公众号链接的唯一人工填写处，不能被空值抹掉
        if url and url.strip() and entry.get("url") != url.strip():
            changes.append(f"~ ai4s.posts[{repo}].url: {entry.get('url')!r} → {url.strip()!r}")
            entry["url"] = url.strip()

    for key in AI4S_POST_KEYS:  # 字段顺序归一（与既有条目一致），不引入额外 key
        if key in entry:
            entry[key] = entry.pop(key)
    return out, changes


def sync_ai4s_post(site_json_path: str | Path | None = None, post_dir: str | Path = AI4S_DIR_DEFAULT,
                   *, date_override: str | None = None, url: str | None = None,
                   summary: str | None = None, name: str | None = None, repo: str | None = None,
                   no: int | None = None, dry_run: bool = False,
                   quiet: bool = False) -> tuple[dict, list[str]]:
    """读 site.json → 从产物目录派生 → 计划 →（非 dry-run 时）原子写回。"""
    path = Path(site_json_path or SITE_JSON_DEFAULT)
    directory = _ai4s_dir(post_dir)
    derived, warnings = derive_ai4s_meta(directory)
    data = _load(path)
    new, changes = plan_sync_ai4s(
        data, repo=repo or derived["repo"], name=name or derived["name"],
        summary=derived["summary"] if summary is None else summary,
        pub_date=date_override or derived["date"], url=url, no=no,
        date_explicit=date_override is not None)
    if not quiet:
        for w in warnings:
            print(f"  [告警] {w}", file=sys.stderr)
        if not (directory / "post-base64.html").exists():
            print(f"  [告警] 未找到 {directory / 'post-base64.html'}（渲染未完成？）", file=sys.stderr)
    if not dry_run:
        _write_atomic(path, _dump(new))
    return new, changes


# ---- CLI -----------------------------------------------------------------

def _cmd_sync(args: argparse.Namespace) -> int:
    try:
        n = _positive_issue(args.issue)
        data, changes = sync_site_json(
            args.site_json, n, week_start=args.week_start, date_override=args.date,
            window_override=args.window, summary=args.summary, dry_run=args.dry_run)
    except SiteSyncError as exc:
        print(f"sitejson 失败：{exc}", file=sys.stderr)
        return 1
    prefix = "[dry-run] " if args.dry_run else ""
    state = "未写盘" if args.dry_run else "已更新"
    print(f"{prefix}site.json {state}：{args.site_json}（第 {n} 期）")
    for c in changes:
        print(f"  {c}")
    if not changes:
        print("  无变化（已是最新）")
    if args.json:
        print(_dump(data), end="")
    return 0


def _cmd_sync_ai4s(args: argparse.Namespace) -> int:
    try:
        data, changes = sync_ai4s_post(
            args.site_json, args.dir, date_override=args.date, url=args.url,
            summary=args.summary, name=args.name, repo=args.repo,
            no=int(args.no) if args.no else None, dry_run=args.dry_run)
    except (SiteSyncError, ValueError) as exc:
        print(f"sitejson 失败：{exc}", file=sys.stderr)
        return 1
    prefix = "[dry-run] " if args.dry_run else ""
    state = "未写盘" if args.dry_run else "已更新"
    print(f"{prefix}site.json {state}：{args.site_json}（AI4S 推文归档）")
    for c in changes:
        print(f"  {c}")
    if not changes:
        print("  无变化（已是最新）")
    ai4s = data.get("ai4s")
    posts = ai4s.get("posts") if isinstance(ai4s, dict) else None
    pending = [p.get("repo") for p in (posts or []) if isinstance(p, dict) and not p.get("url")]
    if pending:
        print("  提示：以下推文尚无公众号链接——发布后在 ai4s.posts[].url 补上（或重跑时传 --url），"
              f"AI4S 页即从「即将发布」变为可点阅读：{', '.join(pending)}")
    if args.json:
        print(_dump(data), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m sitejson", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true", help="离线自测（临时目录 fixture）")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("sync", help="把本期信息写进 site.json（幂等，不 commit/push）")
    p.add_argument("--issue", required=True, help="正整数期号（-1 测试期不支持）")
    p.add_argument("--site-json", default=str(SITE_JSON_DEFAULT), help="site.json 路径")
    p.add_argument("--week-start", default=None, help=f"第 1 期起始日（默认 {WEEK_START_DEFAULT}）")
    p.add_argument("--date", default=None, help="覆盖官网发布日（ISO 日期）")
    p.add_argument("--window", default=None, help="覆盖素材窗口文案")
    p.add_argument("--summary", default=None, help="覆盖归档一句话摘要")
    p.add_argument("--dry-run", action="store_true", help="只打印将发生的变化，不改文件")
    p.add_argument("--json", action="store_true", help="同时打印写入后的完整 site.json")

    pa = sub.add_parser("sync-ai4s", help="把一篇 AI4S 推文写进 site.json 的 ai4s.posts（幂等，只动 ai4s）")
    pa.add_argument("--dir", required=True, help="PubAI4S 产物目录（如 output/PubAI4S/<owner>-<repo>）")
    pa.add_argument("--site-json", default=str(SITE_JSON_DEFAULT), help="site.json 路径")
    pa.add_argument("--date", default=None, help="发布日（ISO；默认取 post.md 修改日）")
    pa.add_argument("--url", default=None, help="公众号链接（唯一人工填写处；不传则保留既有值）")
    pa.add_argument("--summary", default=None, help="覆盖一句话摘要（默认取 post.md 主标题）")
    pa.add_argument("--name", default=None, help="覆盖项目名（默认取主标题 · 之前的部分）")
    pa.add_argument("--repo", default=None, help="覆盖 owner/repo（默认从结尾 GitHub 块推导）")
    pa.add_argument("--no", default=None, help="覆盖序号（默认取现有最大序号 +1）")
    pa.add_argument("--dry-run", action="store_true", help="只打印将发生的变化，不改文件")
    pa.add_argument("--json", action="store_true", help="同时打印写入后的完整 site.json")
    args = ap.parse_args(argv)

    if args.selftest:
        return run_selftest()
    if args.cmd == "sync-ai4s":
        return _cmd_sync_ai4s(args)
    if args.cmd != "sync":
        ap.print_help()
        return 1
    return _cmd_sync(args)


# ---- 离线自测 -------------------------------------------------------------

_FIXTURE = {
    "siteName": "PubEcosphere",
    "tagline": "tagline",
    "github": "https://github.com/piwir/PubEcosphere",
    "currentIssue": 5,
    "issueDate": "2026-09-09",
    "window": "2026-08-31 – 2026-09-06",
    "wechat": {"url": "https://mp.weixin.qq.com/s/OLD", "label": "阅读第 5 期周报"},
    "issues": [
        {"issue": 5, "date": "2026-09-09", "wechatUrl": "https://mp.weixin.qq.com/s/OLD",
         "summary": "PubPeer 周报 · issue 5"},
    ],
    "ai4s": {"github": "https://github.com/piwir/PubAI4S", "posts": []},
}


def run_selftest() -> int:
    """离线断言：派生回归 + 首次写入 + 幂等 + 不覆盖人工数据 + dry-run + 非法期号。"""
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        if not ok:
            failures.append(f"{name}（{detail}）" if detail else name)

    # 1) 派生回归：仓库里已发布的 site.json 必须与公式逐字一致
    if SITE_JSON_DEFAULT.exists():
        live = _load(SITE_JSON_DEFAULT)
        for it in live.get("issues", []):
            n = it.get("issue")
            if isinstance(n, int) and n > 0:
                check(f"回归 issue {n} date", it.get("date") == issue_date(n).isoformat(),
                      f"{it.get('date')!r} != {issue_date(n).isoformat()!r}")
        cur = live.get("currentIssue")
        if isinstance(cur, int) and cur > 0:
            check("回归 currentIssue date", live.get("issueDate") == issue_date(cur).isoformat())
            check("回归 currentIssue window", live.get("window") == window_label(cur))
    else:
        failures.append(f"回归：找不到 {SITE_JSON_DEFAULT}")

    # 2) fixture 全流程
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "site.json"
        path.write_text(_dump(_FIXTURE), encoding="utf-8")

        data, changes = sync_site_json(path, 6, quiet=True)
        check("首次 sync 有变化", bool(changes))
        check("首次 sync currentIssue", data.get("currentIssue") == 6, repr(data.get("currentIssue")))
        check("首次 sync issueDate", data.get("issueDate") == "2026-09-16", repr(data.get("issueDate")))
        check("首次 sync window", data.get("window") == "2026-09-07 – 2026-09-13",
              repr(data.get("window")))
        check("首次 sync issues 头部",
              data.get("issues", [{}])[0] == {"issue": 6, "date": "2026-09-16",
                                              "wechatUrl": "", "summary": "PubPeer 周报 · issue 6"},
              repr(data.get("issues", [{}])[0]))
        check("首次 sync 期号 5 条目保留", data["issues"][1].get("issue") == 5)
        check("首次 sync wechat 归一（url 空 + 默认 label）",
              data.get("wechat") == {"url": "", "label": "阅读第 6 期周报"},
              repr(data.get("wechat")))
        check("首次 sync ai4s 不动", data.get("ai4s") == _FIXTURE["ai4s"])
        check("首次 sync tagline 不动", data.get("tagline") == _FIXTURE["tagline"])
        first = path.read_text(encoding="utf-8")
        check("格式：2 空格缩进 + 末尾换行", first.endswith("}\n") and '\n  "tagline"' in first)

        # 3) 幂等
        _, changes2 = sync_site_json(path, 6, quiet=True)
        check("幂等：第二次无变化", changes2 == [], repr(changes2))
        check("幂等：字节一致", path.read_text(encoding="utf-8") == first)

        # 4) 发布后在 issues[].wechatUrl 补链接 → 派生首页 CTA
        d = json.loads(path.read_text(encoding="utf-8"))
        d["issues"][0]["wechatUrl"] = "https://mp.weixin.qq.com/s/NEW6"
        path.write_text(_dump(d), encoding="utf-8")
        d2, _ = sync_site_json(path, 6, quiet=True)
        check("补链接后 wechat.url", d2["wechat"]["url"] == "https://mp.weixin.qq.com/s/NEW6",
              repr(d2["wechat"]["url"]))
        check("补链接后 wechat.label", d2["wechat"]["label"] == "阅读第 6 期周报",
              repr(d2["wechat"]["label"]))

        # 5) 链接清空 → wechat 归一为默认占位（不保留手工值，永远与 issues 一致）
        d3 = json.loads(path.read_text(encoding="utf-8"))
        d3["issues"][0]["wechatUrl"] = ""
        d3["wechat"] = {"url": "https://manual.example/6", "label": "手工文案"}
        path.write_text(_dump(d3), encoding="utf-8")
        d4, changes5 = sync_site_json(path, 6, quiet=True)
        check("链接清空后 wechat.url 归一",
              d4["wechat"] == {"url": "", "label": "阅读第 6 期周报"},
              repr(d4["wechat"]))
        check("链接清空后打印 label 变化",
              any("wechat.label" in c for c in changes5), repr(changes5))
        _, changes5b = sync_site_json(path, 6, quiet=True)
        check("归一后再跑无变化（幂等）", changes5b == [], repr(changes5b))

        # 6) dry-run 不写盘
        before = path.read_text(encoding="utf-8")
        sync_site_json(path, 6, dry_run=True, quiet=True)
        check("dry-run 不写盘", path.read_text(encoding="utf-8") == before)

        # 7) 非法期号 / 非法 JSON
        for bad in ("0", "-1", "abc"):
            try:
                sync_site_json(path, bad, quiet=True)  # type: ignore[arg-type]
                failures.append(f"期号 {bad!r} 应当报错")
            except SiteSyncError:
                pass
        broken = Path(tmp) / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        try:
            sync_site_json(broken, 6, quiet=True)
            failures.append("非法 JSON 应当报错")
        except SiteSyncError:
            pass

        # 8) AI4S 推文归档（sync-ai4s）
        post_dir = Path(tmp) / "PubAI4S" / "google-deepmind-synthidbio"
        (post_dir / "inputs").mkdir(parents=True)
        (post_dir / "post.md").write_text(
            "# SynthID Bio · 为 AI 生成的蛋白质序列与结构嵌入保功能水印\n\n"
            "正文里还有第三方链接 https://github.com/google-deepmind/alphafold3 与 "
            "https://github.com/astral-sh/uv\n\n"
            "> GitHub：[github.com/google-deepmind/synthidbio]"
            "(https://github.com/google-deepmind/synthidbio)\n", encoding="utf-8")
        (post_dir / "inputs" / "meta.txt").write_text(
            "仓库链接：https://github.com/google-deepmind/synthidbio\n", encoding="utf-8")
        derived, warns = derive_ai4s_meta(post_dir)
        check("ai4s 派生 repo（取结尾 GitHub 块，非正文第三方链接）",
              derived["repo"] == "google-deepmind/synthidbio", repr(derived["repo"]))
        check("ai4s 派生 name", derived["name"] == "SynthID Bio", repr(derived["name"]))
        check("ai4s 派生 summary", derived["summary"].startswith("SynthID Bio · "),
              repr(derived["summary"]))
        check("ai4s 派生无告警", warns == [], repr(warns))

        before_ai4s = json.loads(path.read_text(encoding="utf-8"))
        d5, ch = sync_ai4s_post(path, post_dir, date_override="2026-10-01", quiet=True)
        check("ai4s 首次写入有变化", bool(ch), repr(ch))
        check("ai4s 序号从 1 起", d5["ai4s"]["posts"][0]["no"] == 1, repr(d5["ai4s"]["posts"][0]))
        check("ai4s 只动 ai4s（其余顶层字段逐字不变）",
              {k: v for k, v in d5.items() if k != "ai4s"}
              == {k: v for k, v in before_ai4s.items() if k != "ai4s"})
        check("ai4s 字段顺序", list(d5["ai4s"]["posts"][0]) == list(AI4S_POST_KEYS),
              repr(list(d5["ai4s"]["posts"][0])))

        _, ch2 = sync_ai4s_post(path, post_dir, date_override="2026-10-01", quiet=True)
        check("ai4s 幂等：第二次无变化", ch2 == [], repr(ch2))

        d7 = json.loads(path.read_text(encoding="utf-8"))
        d7["ai4s"]["posts"][0]["url"] = "https://mp.weixin.qq.com/s/AI4S1"
        path.write_text(_dump(d7), encoding="utf-8")
        d8, _ = sync_ai4s_post(path, post_dir, date_override="2026-10-01", quiet=True)
        check("ai4s 不覆盖已有链接（唯一人工填写处）",
              d8["ai4s"]["posts"][0]["url"] == "https://mp.weixin.qq.com/s/AI4S1",
              repr(d8["ai4s"]["posts"][0]["url"]))
        d9, ch9 = sync_ai4s_post(path, post_dir, date_override="2026-10-01",
                                 url="https://mp.weixin.qq.com/s/AI4S2", quiet=True)
        check("ai4s 显式传 url 才更新",
              d9["ai4s"]["posts"][0]["url"].endswith("AI4S2") and any("url" in c for c in ch9),
              repr(ch9))
        check("ai4s 同仓库不重复", len(d9["ai4s"]["posts"]) == 1, repr(d9["ai4s"]["posts"]))

        # 派生 date（post.md 修改日）不得覆盖已有的发布日（补链接再跑一次是常规操作）
        d9b = json.loads(path.read_text(encoding="utf-8"))
        d9b["ai4s"]["posts"][0]["date"] = "2026-10-05"
        path.write_text(_dump(d9b), encoding="utf-8")
        d9c, _ = sync_ai4s_post(path, post_dir, quiet=True)
        check("ai4s 补链接重跑不把发布日改回生成日",
              d9c["ai4s"]["posts"][0]["date"] == "2026-10-05",
              repr(d9c["ai4s"]["posts"][0]["date"]))
        d9d, _ = sync_ai4s_post(path, post_dir, date_override="2026-10-06", quiet=True)
        check("ai4s 显式 --date 才更新日期",
              d9d["ai4s"]["posts"][0]["date"] == "2026-10-06",
              repr(d9d["ai4s"]["posts"][0]["date"]))

        # meta.txt 兜底也要去 scheme（post.md 无结尾 GitHub 块时）
        post_fb = Path(tmp) / "PubAI4S" / "fallback-repo"
        (post_fb / "inputs").mkdir(parents=True)
        (post_fb / "post.md").write_text("# Fallback · 无结尾链接\n", encoding="utf-8")
        (post_fb / "inputs" / "meta.txt").write_text(
            "仓库链接：https://github.com/fallback/repo\n", encoding="utf-8")
        derived_fb, _ = derive_ai4s_meta(post_fb)
        check("ai4s meta.txt 兜底 repo 去 scheme",
              derived_fb["repo"] == "fallback/repo", repr(derived_fb["repo"]))

        post2 = Path(tmp) / "PubAI4S" / "omicverse-omicverse"
        post2.mkdir(parents=True)
        (post2 / "post.md").write_text(
            "# OmicVerse · 一站式分析平台\n\n"
            "> GitHub：[github.com/omicverse/omicverse](https://github.com/omicverse/omicverse)\n",
            encoding="utf-8")
        d10, _ = sync_ai4s_post(path, post2, date_override="2026-10-02", quiet=True)
        check("ai4s 第二条 no=2 且插头部", d10["ai4s"]["posts"][0]["no"] == 2,
              repr(d10["ai4s"]["posts"][0]))
        check("ai4s 已有条目保留",
              any(p["repo"] == "google-deepmind/synthidbio" for p in d10["ai4s"]["posts"]))

        snap = path.read_text(encoding="utf-8")
        sync_ai4s_post(path, post2, dry_run=True, quiet=True)
        check("ai4s dry-run 不写盘", path.read_text(encoding="utf-8") == snap)
        try:
            sync_ai4s_post(path, Path(tmp) / "nonexistent", quiet=True)
            failures.append("ai4s 缺 post.md 应当报错")
        except SiteSyncError:
            pass

    if failures:
        print("sitejson selftest 失败：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("sitejson selftest: 全部通过（派生回归/首次写入/幂等/wechat 归一/dry-run/非法输入/AI4S 归档）")
    return 0
