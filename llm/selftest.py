"""离线自测：不联网、不调模型，验证 llm/ 各模块的行为。

运行：`python -m llm selftest`。用例：
- check：两提示词存在 + 八节齐全。
- client：假 transport 验 POST body 结构；本地 http.server 验真实 urllib 路径；5xx 退避重试。
- vision：真实 material 目录断言图片 parts 数量（含 author/sleuth 图 vs 无）。
- writer：阶段A 样例断言纯文本结构（无图片 part）。
- flatten：临时目录断言扁平命名。
- assemble：合成周报断言重写与缺失告警。
"""
from __future__ import annotations

import json
import shutil
import tempfile
import threading
from pathlib import Path

from .assemble import assemble_weekly
from .client import LLMConfig, LLMClient, LLMError, default_transport, image_part_data_uri
from .flatten import flatten_material
from .prompts import load_prompt, validate_prompt_schema
from .vision import build_vision_messages, collect_images, prepend_metadata
from .writer import build_writer_messages

REPO_ROOT = Path(__file__).resolve().parent.parent
MATERIAL_DIR = REPO_ROOT / "output" / "issue" / "-1" / "material"


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
    img = image_part_data_uri(MATERIAL_DIR / "03302A9467F1BCD7DC095448C18F25" / "first_merged.png")
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
    pids = {
        "568B4CF8B40A979424B7F343F3B061": 2,   # first + author
        "10E90FE934C335D0FB7B953A8936AD": 2,   # first + sleuth
        "03302A9467F1BCD7DC095448C18F25": 1,   # first only
    }
    for pid, expect in pids.items():
        d = MATERIAL_DIR / pid
        imgs = collect_images(d)
        assert len(imgs) == expect, f"{pid}: 图片数 {len(imgs)} != {expect}"
        md = (d / f"{pid}.md").read_text(encoding="utf-8")
        md = prepend_metadata(md, {"category": "测试", "impact": "IF 1.0"})
        assert md.startswith("分类：测试\nIF：1.0\n"), "元数据行未补到顶部"
        messages = build_vision_messages(md, imgs, prompt)
        assert messages[0]["role"] == "system"
        parts = messages[1]["content"]
        n_img = sum(1 for p in parts if p["type"] == "image_url")
        assert n_img == expect, f"{pid}: 图片 parts {n_img} != {expect}"
    return True, "真实 material 目录：图片 parts 数量与 md 元数据补行正确"


def _test_writer() -> tuple[bool, str]:
    prompt = load_prompt("writer")
    stage_a = "## ABC123\n\n### 元数据\n- 分类：测试\n"
    messages = build_writer_messages(stage_a, prompt)
    assert messages[0]["role"] == "system" and prompt in messages[0]["content"]
    assert isinstance(messages[1]["content"], str)
    assert "image_url" not in messages[1]["content"]
    return True, "阶段B messages 为纯文本，无图片 part"


def _test_flatten_assemble() -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # flatten：复制真实的一篇到临时 material，摊平后断言扁平命名
        fake_mat = tmp / "material"
        (fake_mat / "PID0000000000000001").mkdir(parents=True)
        real = MATERIAL_DIR / "568B4CF8B40A979424B7F343F3B061"
        shutil.copy(real / "568B4CF8B40A979424B7F343F3B061.md",
                    fake_mat / "PID0000000000000001" / "PID0000000000000001.md")
        for k in ("first_merged", "author_merged"):
            shutil.copy(real / f"{k}.png", fake_mat / "PID0000000000000001" / f"{k}.png")
        up = tmp / "upload"
        summary = flatten_material(fake_mat, up, manifest={"picks": [
            {"pubpeer_id": "PID0000000000000001", "category": "神经科学", "impact": "IF 16.9"}]})
        assert sorted(f.name for f in up.iterdir()) == [
            "PID0000000000000001.md",
            "PID0000000000000001_author_merged.png",
            "PID0000000000000001_first_merged.png",
        ]
        assert (up / "PID0000000000000001.md").read_text().startswith("分类：神经科学\nIF：16.9\n")
        # assemble：合成草稿 → 断言重写与缺失告警
        weekly = tmp / "weekly"
        draft = weekly / "-1_draft.md"
        weekly.mkdir()
        draft.write_text(
            "![PID:568B4CF8B40A979424B7F343F3B061 质疑人证据图](568B4CF8B40A979424B7F343F3B061_first_merged.png)\n"
            "![PID:568B4CF8B40A979424B7F343F3B061 作者回应图](author_merged.png)\n"
            "![PID:NOPE0000000000000000 不存在图](NOPE0000000000000000_first_merged.png)\n"
            "![无标签图](first_merged.png)\n"
            "![PID:568B4CF8B40A979424B7F343F3B061 质疑人证据图](B5B94D9E8191F48DA52ABDC79E7131_first_merged.png)\n"
            "![PID:568B4CF8B40A979424B7F343F3B061 未知图](568B4CF8B40A979424B7F343F3B061_whatever.png)\n"
            "![PID:10E90FE934C335D0FB7B953A8936AD Elisabeth M Bik 补充图](10E90FE934C335D0FB7B953A8936AD_sleuth_merged.png)\n"
            "本文提及知名打假人参与讨论。\n",
            encoding="utf-8")
        warnings = assemble_weekly(draft, MATERIAL_DIR, weekly, issue="-1")
        out_md = (weekly / "-1.md").read_text(encoding="utf-8")
        assert "568B4CF8B40A979424B7F343F3B061_first_merged.png" in out_md
        assert "568B4CF8B40A979424B7F343F3B061_author_merged.png" in out_md  # 裸名被重写
        assert (weekly / "568B4CF8B40A979424B7F343F3B061_author_merged.png").exists()
        assert "NOPE" in out_md  # 缺失图保留原链接
        assert not (weekly / "NOPE0000000000000000_first_merged.png").exists()  # 缺失图不复制
        assert "568B4CF8B40A979424B7F343F3B061_whatever.png" in out_md  # 未知种类原样保留
        assert any("不一致" in w for w in warnings)
        assert any("无法识别图片种类" in w for w in warnings)
        assert any("不存在" in w for w in warnings)
        assert any("无法解析 pid" in w for w in warnings)
        assert any("打假人姓名" in w for w in warnings)      # sleuth 图 alt 带姓名
        assert any("知名打假人" in w for w in warnings)       # 正文含禁词
        return True, "flatten 扁平命名 / assemble 重写+缺失+统一口径告警正确"


TESTS = [
    ("check 提示词八节", _test_check),
    ("client POST body 结构", _test_client_structure),
    ("client 重试与错误", _test_client_retry),
    ("client 本地 http.server", _test_http_server),
    ("vision 图片 parts 数量", _test_vision),
    ("writer 纯文本结构", _test_writer),
    ("flatten + assemble", _test_flatten_assemble),
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
