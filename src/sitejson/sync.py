"""官网归档核心：`src/site/src/data/site.json` 的确定性、幂等更新。

用法：
    python -m sitejson sync --issue 6              # 把第 6 期写进 site.json（幂等）
    python -m sitejson sync --issue 6 --dry-run    # 只看将发生的变化，不写盘
    python -m sitejson --selftest                  # 离线自测（临时目录 fixture）

派生规则（与已发布第 1–5 期实测值一致）：
    窗口   [WEEK_START+(N-1)*7, +6 天]      → window: "YYYY-MM-DD – YYYY-MM-DD"
    发布日 WEEK_START+(N-1)*7 + 9 天        → issueDate（周三）

写入规则（幂等、绝不覆盖人工数据）：
    1. issues[]：已有第 N 期条目则补 date（summary 仅在为空时补）；否则头部插入
       {issue, date, wechatUrl: "", summary}；
    2. currentIssue / issueDate / window 写成本期派生值；
    3. wechat（首页 CTA）：issues[N].wechatUrl 非空 → 派生 url + "阅读第 N 期周报"；
       否则仅在「currentIssue 正从旧值提升到 N」这一刻清空 → "第 N 期周报即将发布"；
       已提升过且链接仍空时保持原值（尊重手工编辑）。
    4. tagline / github / ai4s 一概不动。

退出码：成功 0；期号非法 / JSON 非法 / 文件缺失 → 1。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from repopath import REPO_ROOT

# 第 1 期数据收集起始日：与 run_issue.sh / llm.generate / agent.tools 同一锚点。
WEEK_START_DEFAULT = "2026-08-03"
SITE_JSON_DEFAULT = REPO_ROOT / "src" / "site" / "src" / "data" / "site.json"

SUMMARY_TEMPLATE = "PubPeer 周报 · issue {n}"
PENDING_LABEL_PREFIX = "第 {n} 期周报即将发布"
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
    if link:
        label = READ_LABEL_PREFIX.format(n=n)
        if wechat.get("url") != link:
            changes.append(f"~ wechat.url: {wechat.get('url')!r} → {link!r}（取自 issues[{n}].wechatUrl）")
        if wechat.get("label") != label:
            changes.append(f"~ wechat.label: {wechat.get('label')!r} → {label!r}")
        wechat["url"], wechat["label"] = link, label
    elif data.get("currentIssue") != n:
        label = PENDING_LABEL_PREFIX.format(n=n)
        changes.append(f"~ wechat.url: {wechat.get('url')!r} → ''（第 {n} 期链接未发布）")
        changes.append(f"~ wechat.label: {wechat.get('label')!r} → {label!r}")
        wechat["url"], wechat["label"] = "", label
    # else：已提升到期号 N、链接仍空 → 保持原值（可能就是手工填的）

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
    args = ap.parse_args(argv)

    if args.selftest:
        return run_selftest()
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
        check("首次 sync wechat 清空",
              data.get("wechat") == {"url": "", "label": "第 6 期周报即将发布"},
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

        # 5) 已提升期号 + 链接仍空 → 不动手工 wechat
        d3 = json.loads(path.read_text(encoding="utf-8"))
        d3["issues"][0]["wechatUrl"] = ""
        d3["wechat"] = {"url": "https://manual.example/6", "label": "手工文案"}
        path.write_text(_dump(d3), encoding="utf-8")
        d4, _ = sync_site_json(path, 6, quiet=True)
        check("已提升期号时保留手工 wechat",
              d4["wechat"] == {"url": "https://manual.example/6", "label": "手工文案"},
              repr(d4["wechat"]))

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

    if failures:
        print("sitejson selftest 失败：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("sitejson selftest: 全部通过（派生回归/首次写入/幂等/不覆盖人工数据/dry-run/非法输入）")
    return 0
