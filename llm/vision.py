"""阶段A 多模态提取：把一篇（或一小批）素材组装成 OpenAI 多模态 messages。

与手动流程输入一致：md 顶部补 `分类`/`IF` 行（取自 manifest.json），
图片按 first/author/sleuth 顺序用 base64 data-URI 发送，文件名以文本标签给出。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

from .client import LLMClient, image_part_data_uri

IMAGE_KINDS = ("first_merged", "author_merged", "sleuth_merged")
KIND_LABEL = {
    "first_merged": "质疑人证据图",
    "author_merged": "作者回应图",
    "sleuth_merged": "其他评论者补充图",   # 统一口径：不写「知名打假人」
}


def collect_images(pid_dir: str | Path) -> list[Path]:
    """返回该篇目录里实际存在的合并图（按 first/author/sleuth 顺序）。"""
    d = Path(pid_dir)
    out: list[Path] = []
    for kind in IMAGE_KINDS:
        p = d / f"{kind}.png"
        if p.exists():
            out.append(p)
    return out


def load_manifest(manifest: str | Path | dict | None) -> dict[str, dict]:
    """manifest.json → {pubpeer_id: {category, impact, ...}}；None 或空则返回 {}。"""
    if manifest is None:
        return {}
    if isinstance(manifest, dict):
        picks = manifest.get("picks", [])
    else:
        picks = json.loads(Path(manifest).read_text(encoding="utf-8")).get("picks", [])
    meta = {}
    for p in picks:
        pid = p.get("pubpeer_id")
        if pid:
            meta[pid] = p
    return meta


def prepend_metadata(md_text: str, meta: dict[str, Any]) -> str:
    """在 md 顶部补 `分类`/`IF` 行（仅当提供），与 flatten 产物格式一致。"""
    header: list[str] = []
    if meta.get("category"):
        header.append(f"分类：{meta['category']}")
    if meta.get("impact"):
        header.append(f"IF：{meta['impact'].replace('IF ', '')}")
    if not header:
        return md_text
    return "\n".join(header) + "\n\n" + md_text


def build_vision_messages(md_text: str, image_paths: Iterable[str | Path],
                          prompt: str) -> list[dict]:
    """组装 OpenAI 消息：system=阶段A 提示词；user=md 文本 + 每张图的标签与 data-URI。"""
    content: list[dict] = [{"type": "text", "text": md_text}]
    for path in image_paths:
        p = Path(path)
        name = p.name
        kind = "unknown"
        for k, label in KIND_LABEL.items():
            if name.endswith(f"_{k}.png") or name == f"{k}.png":
                kind = f"（{label}）"
                break
        content.append({"type": "text", "text": f"【图片 {name}{kind}】"})
        content.append(image_part_data_uri(p))
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": content},
    ]


def run_vision(pid_dir: str | Path, prompt: str, client: LLMClient,
               meta: Optional[dict[str, Any]] = None,
               model: str | None = None) -> str:
    """阶段A 完整调用：读 md + 补元数据 + 打包图片 → 模型返回提取块。"""
    d = Path(pid_dir)
    md_text = (d / f"{d.name}.md").read_text(encoding="utf-8")
    if meta:
        md_text = prepend_metadata(md_text, meta)
    messages = build_vision_messages(md_text, collect_images(d), prompt)
    return client.chat(messages, model=model)
