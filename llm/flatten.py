"""摊平 material/ 为扁平上传文件夹 upload/（手动上传的入口，纯离线）。

- md 复制为 `<pid>.md`，顶部补 `分类`/`IF` 两行（取自 manifest.json，与视觉模型读取一致）。
- 图片复制为 `<pid>_<kind>.png`（first_merged / author_merged / sleuth_merged，存在才复制）。
- 目标目录**无子目录**，方便对话平台里按 pid 前缀框选上传。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Iterable, Optional

from .vision import IMAGE_KINDS, load_manifest


def flatten_material(material_dir: str | Path, upload_dir: str | Path,
                     manifest: Optional[str | Path | dict] = None) -> list[dict]:
    """摊平并复制；返回每篇的清单 [{pid, category, images:[kind,...]}]。"""
    mat = Path(material_dir)
    out = Path(upload_dir)
    out.mkdir(parents=True, exist_ok=True)
    meta = load_manifest(manifest)

    summary: list[dict] = []
    for pid_dir in sorted(p for p in mat.iterdir() if p.is_dir()):
        pid = pid_dir.name
        info = meta.get(pid, {})
        md = pid_dir / f"{pid}.md"
        if md.exists():
            header: list[str] = []
            if info.get("category"):
                header.append(f"分类：{info['category']}")
            if info.get("impact"):
                header.append(f"IF：{info['impact'].replace('IF ', '')}")
            text = md.read_text(encoding="utf-8")
            if header:
                text = "\n".join(header) + "\n\n" + text
            (out / f"{pid}.md").write_text(text, encoding="utf-8")
        images: list[str] = []
        for kind in IMAGE_KINDS:
            src = pid_dir / f"{kind}.png"
            if src.exists():
                shutil.copy2(src, out / f"{pid}_{kind}.png")
                images.append(kind)
        if not (pid_dir / f"{pid}.md").exists() and images:
            print(f"  [告警] {pid}: 有图片但缺 {pid}.md（已复制图片，未补 md）", file=sys.stderr)
        summary.append({"pid": pid, "category": info.get("category", ""), "images": images})
    return summary


def print_flatten_summary(summary: Iterable[dict]) -> None:
    rows = list(summary)
    n_md = len(rows)
    n_png = sum(len(r["images"]) for r in rows)
    print(f"扁平上传文件夹已生成（无子目录）：md {n_md} 个 + 图 {n_png} 张 = {n_md + n_png} 个文件")
    for r in rows:
        print(f"  {r['pid']}  [{r['category']}]  "
              + (" ".join(f"{k}.png" for k in r["images"]) if r["images"] else "(无图!)"))
