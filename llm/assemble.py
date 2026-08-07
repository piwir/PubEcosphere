"""排版：把阶段B 输出的周报草稿 md 变成成品（纯离线，手动流程也用）。

- 把草稿里引用的图片统一成扁平 `<pid>_<kind>.png`（裸文件名 `first_merged.png`
  也会重写为 `<pid>_first_merged.png`），并把图从 material/<pid>/ 复制到与 md 同目录。
- 校验每张图：alt 是否带 `PID:<pid>` 前缀、material 里是否真存在；缺失/无标签打印告警并原样保留。
- 输出 `weekly/<issue>.md`（md 与图同目录，baoyu 以 md 目录为 baseDir 即可解析）。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

IMAGE_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
PID_TAG_RE = re.compile(r"PID:([0-9A-Fa-f]{6,})")
KINDS = ("first_merged", "author_merged", "sleuth_merged")

# 打假人署名统一口径守护：与 scoring/config.py 的 sleuths 保持一致（勿回退）。
# 仅用于扫描告警，绝不写入产出。归一化后的小写整串。
SLEUTH_KEYS = (
    "elisabeth m bik", "hoya camphorifolia", "sholto david", "leonid schneider",
    "matthew schrag", "david sanders", "clare francis",
)


def _norm(s: str) -> str:
    """小写 + 非字母数字折叠为空格（与 scoring/signals.py 的 _norm_name 同一归一化）。"""
    return re.sub(r"[^a-z0-9]+", " ", s.lower())


def _pid_from_name(name: str) -> str | None:
    """从 `<pid>_<kind>.png` 里抽出 pid；裸文件名返回 None。"""
    m = re.match(r"^([0-9A-Fa-f]{16,})_(.+?)\.png$", name)
    return m.group(1) if m else None


def _kind_from_name(name: str) -> str | None:
    stem = name[:-4] if name.endswith(".png") else name
    for k in KINDS:
        if stem == k or stem.endswith(f"_{k}"):
            return k
    return None


def assemble_weekly(draft_path: str | Path, material_dir: str | Path,
                    out_dir: str | Path, issue: str = "", dry_run: bool = False) -> list[str]:
    """重写草稿、复制图片到 out_dir，写 `{issue}.md`。返回告警列表。"""
    draft = Path(draft_path)
    mat = Path(material_dir)
    out = Path(out_dir)
    text = draft.read_text(encoding="utf-8")
    warnings: list[str] = []

    def rewrite(match: re.Match) -> str:
        alt, link = match.group(1), match.group(2)
        name = Path(link.split()[0]).name if link else ""
        if not name.lower().endswith(".png"):
            return match.group(0)
        pid_alt = (PID_TAG_RE.search(alt).group(1) if PID_TAG_RE.search(alt) else None)
        pid_name = _pid_from_name(name)
        kind = _kind_from_name(name)
        if pid_alt and pid_name and pid_alt != pid_name:
            warnings.append(f"alt PID({pid_alt}) 与文件名 pid({pid_name}) 不一致：{name}")
        pid = pid_alt or pid_name
        if not pid:
            warnings.append(f"无法解析 pid（alt 缺 PID: 标签）：{alt} → {name}")
            return match.group(0)
        if not kind:
            warnings.append(f"无法识别图片种类（应为 first/author/sleuth_merged）：{name}")
            return match.group(0)
        src = mat / pid / f"{kind}.png"
        if not src.exists():
            warnings.append(f"material 中不存在 {pid}/{kind}.png（被引用了：{name}）")
            return match.group(0)
        target = f"{pid}_{kind}.png"
        if kind == "sleuth_merged" and any(key in _norm(alt) for key in SLEUTH_KEYS):
            warnings.append(f"其他评论者补充图 alt 疑似含打假人姓名（违反统一口径）：{alt}")
        if not dry_run:
            out.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out / target)
        return f"![{alt}]({target})"

    rewritten = IMAGE_REF_RE.sub(rewrite, text)
    if "知名打假人" in rewritten:
        warnings.append("产出含「知名打假人」字样，违反打假人署名统一口径")
    if not dry_run:
        out.mkdir(parents=True, exist_ok=True)
        out_md = out / (f"{issue}.md" if issue else draft.name.replace("_draft", ""))
        out_md.write_text(rewritten, encoding="utf-8")
        print(f"已写出：{out_md}")
    return warnings
