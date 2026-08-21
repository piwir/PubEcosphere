"""素材目录与 manifest 的共享工具：合并图收集 / manifest 元数据 / md 顶部补行。

供 `generate`（单模型端到端）与 `flatten`（摊平上传文件夹）复用。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

IMAGE_KINDS = ("first_merged", "author_merged", "sleuth_merged")
KIND_LABEL = {
    "first_merged": "质疑人证据图",
    "author_merged": "作者回应图",
    "sleuth_merged": "其他评论者补充图",   # 统一口径：不写「知名打假人」
}


def merged_variants(pid_dir: str | Path, kind: str) -> list[Path]:
    """某类合并图在该篇目录里实际存在的全部变体：{kind}.png、{kind}_2.png、{kind}_3.png…。

    序号连续递增（material 拆分命名），断档即止。
    """
    d = Path(pid_dir)
    out: list[Path] = []
    for i in range(1, 100):
        name = f"{kind}.png" if i == 1 else f"{kind}_{i}.png"
        p = d / name
        if p.exists():
            out.append(p)
        elif i > 1:
            break
    return out


def collect_images(pid_dir: str | Path) -> list[Path]:
    """返回该篇目录里实际存在的合并图（按 first/author/sleuth 顺序，同类多张按序号）。"""
    d = Path(pid_dir)
    out: list[Path] = []
    for kind in IMAGE_KINDS:
        out.extend(merged_variants(d, kind))
    return out


def kind_label(name: str) -> str:
    """按文件名给图片类别标签：first_merged.png → 质疑人证据图；first_merged_2.png → 质疑人证据图（第 2 张）。"""
    for kind, label in KIND_LABEL.items():
        if name == f"{kind}.png":
            return label
        m = re.match(rf"^{re.escape(kind)}_(\d+)\.png$", name)
        if m:
            return f"{label}（第 {m.group(1)} 张）"
    return "图片"


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
