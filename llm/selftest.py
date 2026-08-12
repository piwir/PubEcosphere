"""离线自测：不联网、不调模型，验证 llm/ 各模块的行为。

运行：`python -m llm selftest`。用例：
- check：三份提示词存在 + 八节齐全。
- client：假 transport 验 POST body 结构；本地 http.server 验真实 urllib 路径；5xx 退避重试。
- vision：合成临时 material 断言图片 parts 数量（含 _N 变体）。
- writer：阶段A 样例断言纯文本结构（无图片 part）。
- flatten：临时目录断言扁平命名（含 `_2` 变体）。
- assemble：合成周报断言重写（含 _N 源图查找）与缺失/统一口径告警。
- generate：假 transport 断言 stageA_combined 归集 + 草稿写出。
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
from .client import LLMConfig, LLMClient, LLMError, default_transport, image_part_data_uri
from .flatten import flatten_material
from .generate import generate_issue, material_papers
from .prompts import load_prompt, validate_prompt_schema
from .vision import build_vision_messages, collect_images, prepend_metadata
from .writer import build_writer_messages

REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_png(width: int = 12, height: int = 8, color: tuple = (255, 0, 0)) -> bytes:
    """内存里合成一张极小 PNG（自测用，不依赖真实素材目录）。"""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _make_paper_dir(root: Path, pid: str, kinds: dict[str, int], md_extra: str = "") -> Path:
    """合成一篇 material 目录：<root>/<pid>/<pid>.md + 按 kinds 生成合并图（值=该类的张数）。

    kinds: {"first_merged": 2, "author_merged": 1, "sleuth_merged": 0}
    """
    d = root / pid
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
    assert body["messages"][0]["content"] == "hello"
    with tempfile.TemporaryDirectory() as tmp:
        img_path = Path(tmp) / "img.png"
        img_path.write_bytes(_make_png())
        img = image_part_data_uri(img_path)
        assert img["type"] == "image_url"
        assert img["image_url"]["url"].startswith("data:image/png;base64,")
    return True, "POST body / data-URI part 结构正确"


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
        assert body["model"] == cfg.writer_model
        assert body["messages"][0]["content"] == "ping"
        return True, "真实 urllib → http.server 收发正常"
    finally:
        srv.shutdown()


def _test_vision() -> tuple[bool, str]:
    prompt = load_prompt("vision")
    with tempfile.TemporaryDirectory() as tmp:
        mat = Path(tmp)
        # 含 _2 变体：first 2 张 + author 1 张 + sleuth 0 → 共 3 parts
        _make_paper_dir(mat, "PID0000000000000001", {"first_merged": 2, "author_merged": 1, "sleuth_merged": 0})
        d = mat / "PID0000000000000001"
        imgs = collect_images(d)
        names = [p.name for p in imgs]
        assert names == ["first_merged.png", "first_merged_2.png", "author_merged.png"], names
        md = (d / "PID0000000000000001.md").read_text(encoding="utf-8")
        md = prepend_metadata(md, {"category": "测试", "impact": "IF 1.0"})
        assert md.startswith("分类：测试\nIF：1.0\n"), "元数据行未补到顶部"
        messages = build_vision_messages(md, imgs, prompt)
        parts = messages[1]["content"]
        n_img = sum(1 for p in parts if p["type"] == "image_url")
        assert n_img == 3, f"图片 parts {n_img} != 3"
        assert any("first_merged_2" in p["text"] for p in parts if p["type"] == "text")
        # 断档：只有 _3 没有 _2 时 _3 应不被收集
        (d / "author_merged_3.png").write_bytes(_make_png())
        assert [p.name for p in collect_images(d)] == names, "断档变体不应被收集"
    return True, "合成 material：图片 parts 数量 / _N 变体收集 / 元数据补行正确"


def _test_writer() -> tuple[bool, str]:
    prompt = load_prompt("writer")
    stage_a = "## ABC123\n\n### 元数据\n- 分类：测试\n"
    messages = build_writer_messages(stage_a, prompt)
    assert messages[0]["role"] == "system" and prompt in messages[0]["content"]
    assert isinstance(messages[1]["content"], str)
    assert "image_url" not in messages[1]["content"]
    return True, "阶段B messages 为纯文本，无图片 part"


_PID = "A1B2C3D4E5F60718293A4B5C6D7E8F90"  # 32 位 hex，与真实 PubPeer id 格式一致


def _test_flatten_assemble() -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        fake_mat = tmp / "material"
        _make_paper_dir(fake_mat, _PID, {"first_merged": 2, "author_merged": 1, "sleuth_merged": 0})
        up = tmp / "upload"
        summary = flatten_material(fake_mat, up, manifest={"picks": [
            {"pubpeer_id": _PID, "category": "神经科学", "impact": "IF 16.9"}]})
        assert sorted(f.name for f in up.iterdir()) == [
            f"{_PID}.md",
            f"{_PID}_author_merged.png",
            f"{_PID}_first_merged.png",
            f"{_PID}_first_merged_2.png",
        ]
        assert (up / f"{_PID}.md").read_text().startswith("分类：神经科学\nIF：16.9\n")
        assert [im for r in summary for im in r["images"]] == ["first_merged", "first_merged_2", "author_merged"]
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
        return True, "flatten 扁平命名（含 _2）/ assemble 重写+缺失+统一口径告警正确"


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


TESTS = [
    ("check 提示词八节", _test_check),
    ("client POST body 结构", _test_client_structure),
    ("client 重试与错误", _test_client_retry),
    ("client 截断检测", _test_client_truncation),
    ("client 本地 http.server", _test_http_server),
    ("vision 图片 parts 数量", _test_vision),
    ("writer 纯文本结构", _test_writer),
    ("flatten + assemble", _test_flatten_assemble),
    ("generate 单模型离线", _test_generate),
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
