"""素材目录与 manifest 的共享工具：manifest 元数据 / md 顶部补行。

供 `generate`（单模型端到端）与 `assemble`（排版）复用。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    """在 md 顶部补 `分类`/`IF` 行（仅当提供），与素材 md 元数据格式一致。"""
    header: list[str] = []
    if meta.get("category"):
        header.append(f"分类：{meta['category']}")
    if meta.get("impact"):
        header.append(f"IF：{meta['impact'].replace('IF ', '')}")
    if not header:
        return md_text
    return "\n".join(header) + "\n\n" + md_text
