"""离线自测：不联网、不调模型，验证 llm/ 各模块的行为。

运行：`python -m llm selftest`。用例：
- check：两份提示词存在 + 八节齐全。
- client：假 transport 验 POST body 结构；本地 http.server 验真实 urllib 路径；5xx 退避重试。
- assemble：合成周报断言重写（含 _N 源图查找）与缺失/统一口径告警。
- generate：假 transport 断言 stageA_combined 归集 + 草稿写出。
- polish_html：上下标转换 / URL·<a> 保护 / 幂等。
- date_range：WEEK_START + 期号推算数据收集窗口。
所有图片均为合成 fixture（不依赖真实 output/ 目录，干净可移植）。
"""
from __future__ import annotations

import io
import json
import tempfile
import threading
from pathlib import Path

from PIL import Image

from .assemble import assemble_weekly
from .client import LLMConfig, LLMClient, LLMError, default_transport
from .generate import generate_issue, issue_header, material_papers, weekly_date_range
from .polish_html import polish_html, repair_github_links, style_footer_links
from .prompts import validate_prompt_schema

REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_png(width: int = 12, height: int = 8, color: tuple = (255, 0, 0)) -> bytes:
    """内存里合成一张极小 PNG（自测用，不依赖真实素材目录）。"""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _make_paper_dir(root: Path, pid: str, kinds: dict[str, int], md_extra: str = "") -> Path:
    """合成一篇素材目录：<root>/<pid>_files/<pid>.md + 按 kinds 生成合并图（值=该类的张数）。

    kinds: {"first_merged": 2, "author_merged": 1, "sleuth_merged": 0}
    """
    d = root / f"{pid}_files"
    d.mkdir(parents=True, exist_ok=True)
    body = (f"# 某论文标题（英文）\n\n- 期刊：Some Journal\n- 质疑人：hoya camphorifolia\n"
            f"## 质疑人证据图（推文用）\n")
    for kind, n in kinds.items():
        for i in range(1, n + 1):
            name = f"{kind}.png" if i == 1 else f"{kind}_{i}.png"
            (d / name).write_bytes(_make_png())
            if kind.startswith("first"):
                body += f"![质疑人证据图]({name})\n"
        if n == 0 and kind.startswith("first"):
            body += "（首位质疑人评论未附图）\n"
    body += md_extra
    (d / f"{pid}.md").write_text(body, encoding="utf-8")
    return d


def _test_check() -> tuple[bool, str]:
    problems = validate_prompt_schema()
    return (not problems), ("OK" if not problems else "; ".join(problems))


def _test_client_structure() -> tuple[bool, str]:
    captured: dict = {}

    def fake_transport(url, payload, headers, timeout):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return 200, {"choices": [{"message": {"content": "hi"}}]}

    cfg = LLMConfig(base_url="https://example.com/v1", api_key="k", backoff=0.01)
    client = LLMClient(cfg, transport=fake_transport)
    out = client.chat([{"role": "user", "content": "hello"}])
    assert out == "hi"
    assert captured["url"] == "https://example.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer k"
    body = captured["payload"]
    assert body["model"] == cfg.model
    assert body["messages"][0]["content"] == "hello"
    return True, "POST body 结构正确（model 缺省用 config.model）"


def _test_client_retry() -> tuple[bool, str]:
    calls = {"n": 0}

    def flaky(url, payload, headers, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            return 503, {}
        return 200, {"choices": [{"message": {"content": "ok"}}]}

    cfg = LLMConfig(base_url="http://x/v1", api_key="k", backoff=0.01)
    out = LLMClient(cfg, transport=flaky).chat([{"role": "user", "content": "hi"}])
    assert out == "ok" and calls["n"] == 3

    def always_500(url, payload, headers, timeout):
        return 500, {}

    try:
        LLMClient(cfg, transport=always_500).chat([{"role": "user", "content": "hi"}])
        return False, "500 应当抛 LLMError 但未抛"
    except LLMError:
        return True, "503 重试成功 / 500 退避后抛 LLMError"


def _test_client_truncation() -> tuple[bool, str]:
    def truncated(url, payload, headers, timeout):
        return 200, {"choices": [{"message": {"content": "残"}, "finish_reason": "length"}]}

    def missing_finish(url, payload, headers, timeout):
        return 200, {"choices": [{"message": {"content": "ok"}}]}

    cfg = LLMConfig(base_url="http://x/v1", api_key="k", backoff=0.01)

    # 1) finish_reason=length → 必须抛 LLMError 且提示调大 max_tokens
    try:
        LLMClient(cfg, transport=truncated).chat([{"role": "user", "content": "hi"}])
        return False, "finish_reason=length 应当抛 LLMError 但未抛"
    except LLMError as exc:
        if "max_tokens" not in str(exc):
            return False, f"截断报错应提示调大 max_tokens，实际：{exc}"

    # 2) 响应缺 finish_reason 字段（如离线假 transport）→ 正常返回，不误报
    out = LLMClient(cfg, transport=missing_finish).chat([{"role": "user", "content": "hi"}])
    if out != "ok":
        return False, "无 finish_reason 字段时应正常返回"
    return True, "finish_reason=length 抛 LLMError 且提示 max_tokens；缺字段正常返回"


def _test_http_server() -> tuple[bool, str]:
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.server.captured_body = self.rfile.read(
                int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(
                {"choices": [{"message": {"content": "from-server"}}]}).encode())

        def log_message(self, *args):  # noqa: A003
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    srv.captured_body = None
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        cfg = LLMConfig(base_url=f"http://127.0.0.1:{srv.server_port}/v1", api_key="k")
        client = LLMClient(cfg, transport=default_transport)
        out = client.chat([{"role": "user", "content": "ping"}])
        assert out == "from-server"
        body = json.loads(srv.captured_body)
        assert body["model"] == cfg.model
        assert body["messages"][0]["content"] == "ping"
        return True, "真实 urllib → http.server 收发正常"
    finally:
        srv.shutdown()


_PID = "A1B2C3D4E5F60718293A4B5C6D7E8F90"  # 32 位 hex，与真实 PubPeer id 格式一致


def _test_assemble() -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        fake_mat = tmp / "material"
        _make_paper_dir(fake_mat, _PID, {"first_merged": 2, "author_merged": 1, "sleuth_merged": 0})
        # assemble：合成草稿 → 断言 _N 源图查找、重写与告警
        weekly = tmp / "weekly"
        weekly.mkdir()
        draft = weekly / "-1_draft.md"
        draft.write_text(
            f"![PID:{_PID} 质疑人证据图]({_PID}_first_merged.png)\n"
            f"![PID:{_PID} 质疑人证据图（第 2 张）]({_PID}_first_merged_2.png)\n"
            f"![PID:{_PID} 作者回应图](author_merged.png)\n"
            "![PID:NOPE0000000000000000 不存在图](NOPE0000000000000000_first_merged.png)\n"
            f"![PID:{_PID} 未知图]({_PID}_whatever.png)\n"
            "本文提及知名打假人参与讨论。\n",
            encoding="utf-8")
        warnings = assemble_weekly(draft, fake_mat, weekly, issue="-1")
        out_md = (weekly / "-1.md").read_text(encoding="utf-8")
        assert f"{_PID}_first_merged.png" in out_md
        assert f"{_PID}_first_merged_2.png" in out_md      # _N 保留
        assert f"{_PID}_author_merged.png" in out_md        # 裸名被重写
        assert (weekly / f"{_PID}_first_merged_2.png").exists()
        assert "NOPE" in out_md  # 缺失图保留原链接
        assert not (weekly / "NOPE0000000000000000_first_merged.png").exists()
        assert f"{_PID}_whatever.png" in out_md  # 未知种类原样保留
        assert any("无法识别图片种类" in w for w in warnings)
        assert any("不存在" in w for w in warnings)
        assert any("知名打假人" in w for w in warnings)       # 正文含禁词
        return True, "assemble 重写（含 _N 源图）+ 缺失/统一口径告警正确"


def _test_generate() -> tuple[bool, str]:
    """generate 离线结构：假 transport 返回罐头提取块 → 断言归集 + 写稿 + 排版。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        mat = tmp / "material"
        _make_paper_dir(mat, _PID, {"first_merged": 1, "sleuth_merged": 0, "author_merged": 0})
        assert len(material_papers(mat)) == 1

        calls = {"n": 0}
        CANNED_EXTRACT = (f"## {_PID}\n\n### 元数据\n- 标题（英文）：Title\n"
                          "- 标题（中文翻译）：标题\n- 分类：测试\n\n"
                          "### 5. 可用图片清单\n- first_merged.png：（评论未描述图内内容）\n")
        CANNED_DRAFT = ("![PubPeer 周报 · PubEcosphere](intro.png)\n\n"
                        f"### 标题 · Some Journal\n\n**看点**：测试看点。\n\n"
                        f"![PID:{_PID} 质疑人证据图]({_PID}_first_merged.png)\n")

        def fake_transport(url, payload, headers, timeout):
            calls["n"] += 1
            prompt = payload["messages"][0]["content"]
            # 写稿提示词含「看点」（卡片模板），提取提示词不含 → 可区分两阶段
            content = CANNED_DRAFT if "看点" in prompt else CANNED_EXTRACT
            return 200, {"choices": [{"message": {"content": content}}]}

        cfg = LLMConfig(base_url="https://example.com/v1", api_key="k", backoff=0.01)
        client = LLMClient(cfg, transport=fake_transport)
        weekly = tmp / "weekly"
        warnings = generate_issue(mat, weekly, issue="-1", client=client, config=cfg,
                                  manifest={"picks": [
                                      {"pubpeer_id": _PID, "category": "测试", "impact": "IF 1.0"}]},
                                  assemble=True)
        assert not warnings, warnings
        combined = (weekly / "stageA_combined.md").read_text(encoding="utf-8")
        assert "期号：-1" in combined and _PID in combined
        draft = (weekly / "-1_draft.md").read_text(encoding="utf-8")
        assert "测试看点" in draft
        final = (weekly / "-1.md").read_text(encoding="utf-8")
        assert f"{_PID}_first_merged.png" in final        # assemble 复制了图
        assert (weekly / f"{_PID}_first_merged.png").exists()
        assert calls["n"] == 2, f"预期 2 次调用（extract+writer），实际 {calls['n']}"
        return True, "generate 假 transport：extract+writer 各一次 / stageA 归集 / 草稿 / assemble 正确"


def _test_polish_html() -> tuple[bool, str]:
    """polish_html：上标/下标转换、URL·DOI·<a> 保护、实体回放、幂等。"""
    src = (
        "Foxp3^(DTR-GFP/y) 与 CD4^t，数量级 10^6 与 2.5×10^6，变量 w_1,...,w_n 与 _{ij}；"
        "链接 https://example.com/a_b 与 DOI 10.1234/test_1；"
        "无 scheme 链接 doi.org/10.1000/abc_def 与 pubpeer.com/publications/ABC_1；"
        '<a href="https://example.com/x^y">链接^内容</a>；&amp; 实体。'
    )
    out = polish_html(src)
    SUP = 'style="font-size:75%; vertical-align:super; line-height:1;"'
    SUB = 'style="font-size:75%; vertical-align:sub; line-height:1;"'
    assert f'Foxp3<sup {SUP}>DTR-GFP/y</sup>' in out
    assert f"CD4<sup {SUP}>t</sup>" in out
    assert f"10<sup {SUP}>6</sup>" in out and f"2.5×10<sup {SUP}>6</sup>" in out
    assert f"w<sub {SUB}>1</sub>" in out and f"w<sub {SUB}>n</sub>" in out and f"<sub {SUB}>ij</sub>" in out
    assert "https://example.com/a_b" in out, "URL 内的 _ 被误转"          # URL 保护
    assert "10.1234/test_1" in out, "DOI 内的 _ 被误转"                   # DOI 保护
    assert "doi.org/10.1000/abc_def" in out, "无 scheme URL 内 _ 被误转"    # 裸域名 URL 保护
    assert "pubpeer.com/publications/ABC_1" in out, "裸域名 URL 内 _ 被误转"
    assert "链接^内容" in out, "a 标签内文本被改写"                        # <a> 保护
    assert "&amp;" in out, "字符实体被破坏"
    assert polish_html(out) == out, "非幂等"                              # 幂等
    return True, "polish_html：上下标转换 / URL·DOI·裸域名URL·a 保护 / 实体 / 幂等正确"


def _test_footer_style() -> tuple[bool, str]:
    """style_footer_links：页脚 PubPeer/DOI 链接 p 注入英文标题同款小字紧排，幂等。"""
    src = (
        '<section class="container" style="font-family: x; font-size: 16px; line-height: 1.75;">'
        '<h3 class="h3" style="line-height: 1.5;">中文标题</h3>'
        '<blockquote class="blockquote"><p class="p" style="display: block; font-size: 1em;'
        ' letter-spacing: 0.1em; color: #3f3f3f; margin: 0;">英文标题</p></blockquote>'
        '<p class="p"><strong>现状</strong>：未提及撤稿或更正。</p>'
        '<blockquote class="blockquote"><p class="p" style="display: block; font-size: 1em;'
        ' letter-spacing: 0.1em; color: #3f3f3f; margin: 0;">PubPeer 讨论：'
        "https://pubpeer.com/publications/ABC<br>DOI：https://doi.org/10.1234/abc</p></blockquote>"
        "</section>"
    )
    out = style_footer_links(src)
    # 页脚 p：小字紧排注入，其它属性保留
    assert (
        'style="font-size: calc(16px * 0.85); line-height: 1.3; display: block;'
        ' letter-spacing: 0.1em; color: #3f3f3f; margin: 0;">PubPeer 讨论：' in out
    ), out
    assert out.count("PubPeer 讨论：") == 1
    # 英文标题 p 不受影响（只命中以 PubPeer 讨论开头的 p）
    assert out.count("font-size: 1em") == 1, "英文标题 p 被误改"
    # 幂等
    assert style_footer_links(out) == out, "非幂等"
    return True, "style_footer_links：页脚小字紧排 / 只命中页脚 / 幂等正确"


def _test_repair_github() -> tuple[bool, str]:
    """repair_github_links：无 scheme / 带 scheme 的固定 GitHub 纯文本统一归一成
    可点击 <a>（href 完整、显示不带 https://），幂等。"""
    new_plain = "GitHub：github.com/piwir/PubEcosphere"
    old_plain = "GitHub：https://github.com/piwir/PubEcosphere"
    expected = ('GitHub：<a href="https://github.com/piwir/PubEcosphere">'
                'github.com/piwir/PubEcosphere</a>')
    assert repair_github_links(new_plain) == expected, "无 scheme 纯文本未修复"
    assert repair_github_links(old_plain) == expected, "带 scheme 纯文本未修复"
    assert repair_github_links(expected) == expected, "已修复的再次运行被改写（非幂等）"
    return True, "repair_github_links：新/旧纯文本归一成可点击 <a>、幂等正确"


def _test_date_range() -> tuple[bool, str]:
    """weekly_date_range：默认 WEEK_START=2026-08-03，每期 +7 天，格式 M.DD–M.DD。"""
    assert weekly_date_range(1) == "8.03–8.09", weekly_date_range(1)
    assert weekly_date_range(2) == "8.10–8.16", weekly_date_range(2)
    assert weekly_date_range(3, "2026-01-05") == "1.19–1.25", weekly_date_range(3, "2026-01-05")
    return True, "date_range：期号→7 天窗口、跨月格式、起始覆盖正确"


def _test_issue_header() -> tuple[bool, str]:
    """issue_header：str/int 期号都注入 `数据收集：`；空/-1/0 不注入。"""
    assert issue_header("1", "2026-08-12") == "期号：1（run 2026-08-12）\n数据收集：8.03–8.09", issue_header("1", "2026-08-12")
    assert issue_header(1, "2026-08-12") == "期号：1（run 2026-08-12）\n数据收集：8.03–8.09", issue_header(1, "2026-08-12")
    assert issue_header("-1", "2026-08-12") == "期号：-1（run 2026-08-12）", issue_header("-1", "2026-08-12")
    assert issue_header("", "2026-08-12") == "期号：-（run 2026-08-12）", issue_header("", "2026-08-12")
    return True, "issue_header：str/int 均注入、测试期号不注入"


TESTS = [
    ("check 提示词八节", _test_check),
    ("client POST body 结构", _test_client_structure),
    ("client 重试与错误", _test_client_retry),
    ("client 截断检测", _test_client_truncation),
    ("client 本地 http.server", _test_http_server),
    ("assemble 图重写与告警", _test_assemble),
    ("generate 单模型离线", _test_generate),
    ("polish_html 上下标", _test_polish_html),
    ("footer 页脚小字紧排", _test_footer_style),
    ("repair_github 链接修复", _test_repair_github),
    ("date_range 日期窗口", _test_date_range),
    ("issue_header 头部注入", _test_issue_header),
]


def run_selftest() -> list[tuple[str, bool, str]]:
    results = []
    for name, fn in TESTS:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append((name, ok, detail))
    return results


if __name__ == "__main__":
    results = run_selftest()
    for name, ok, detail in results:
        print(f"{'[PASS]' if ok else '[FAIL]'} {name}: {detail}")
    if not all(ok for _, ok, _ in results):
        raise SystemExit(1)
