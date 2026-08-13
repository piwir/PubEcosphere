"""排版：把阶段B 输出的周报草稿 md 变成成品（纯离线，手动流程也用）。

- 把草稿里引用的图片统一成扁平 `<pid>_<flat>.png`（裸文件名 `first_merged.png`
  也会重写为 `<pid>_first_merged.png`），并把图从 material/<pid>/ 复制到与 md 同目录。
  同一类合并图可能有多张（`<pid>_first_merged.png` / `<pid>_first_merged_2.png`），
  序号后缀 `_N` 原样保留：material 里找 `first_merged_2.png`、成品写 `<pid>_first_merged_2.png`。
- 校验每张图：alt 是否带 `PID:<pid>` 前缀、material 里是否真存在；缺失/无标签打印告警并原样保留。
- 输出 `weekly/<issue>.md`（md 与图同目录，md2html 以 md 目录为 baseDir 即可解析）。
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

# 固定简介图：由写稿提示词作为固定块输出引用（intro.png），这里只把 images/intro.png 复制到
# 与 md 同目录，供 md2html/inline_images 解析；文案以写稿提示词为唯一来源，不经排版阶段。
INTRO_IMG = "intro.png"          # 复制到 weekly 目录后的裸文件名
INTRO_SRC = Path(__file__).resolve().parent.parent / "images" / "intro.png"


def _norm(s: str) -> str:
    """小写 + 非字母数字折叠为空格（与 scoring/signals.py 的 _norm_name 同一归一化）。"""
    return re.sub(r"[^a-z0-9]+", " ", s.lower())


def _split_name(name: str) -> tuple[str | None, str | None]:
    """拆 `<pid>_<flat>.png` → (pid, flat)；flat 是 material 里的裸文件名（如 first_merged / first_merged_2）。

    裸文件名（无 pid 前缀）时 pid 为 None、flat 为原名。
    """
    m = re.match(r"^([0-9A-Fa-f]{16,})_(.+?)\.png$", name)
    if m:
        return m.group(1), m.group(2)
    if name.endswith(".png"):
        return None, name[:-4]
    return None, None


def _base_kind(flat: str) -> str | None:
    """flat 的基础种类（去 `_N` 序号）：first_merged_2 → first_merged；未知返回 None。"""
    stem = re.sub(r"_\d+$", "", flat)
    return stem if stem in KINDS else None


def copy_intro(out: Path) -> list[str]:
    """把固定简介图 images/intro.png 复制到 out 目录（与 md 同目录，md2html/inline_images 才能解析）。

    简介图与文案由写稿提示词作为固定块输出，这里只补文件 + 校验。返回告警。
    """
    out.mkdir(parents=True, exist_ok=True)
    if INTRO_SRC.exists():
        shutil.copy2(INTRO_SRC, out / INTRO_IMG)
        return []
    return [f"{INTRO_SRC.relative_to(INTRO_SRC.parent.parent)} 缺失，简介图未复制"]


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
        if name == INTRO_IMG:
            return match.group(0)   # 固定简介图，跳过处理（非评论合并图，不告警）
        if not name.lower().endswith(".png"):
            return match.group(0)
        pid_alt = (PID_TAG_RE.search(alt).group(1) if PID_TAG_RE.search(alt) else None)
        pid_name, flat = _split_name(name)
        if pid_alt and pid_name and pid_alt != pid_name:
            warnings.append(f"alt PID({pid_alt}) 与文件名 pid({pid_name}) 不一致：{name}")
        pid = pid_alt or pid_name
        if not pid:
            warnings.append(f"无法解析 pid（alt 缺 PID: 标签）：{alt} → {name}")
            return match.group(0)
        base = _base_kind(flat or name)
        if not base:
            warnings.append(f"无法识别图片种类（应为 first/author/sleuth_merged）：{name}")
            return match.group(0)
        src = mat / pid / f"{flat}.png"
        if not src.exists():
            warnings.append(f"material 中不存在 {pid}/{flat}.png（被引用了：{name}）")
            return match.group(0)
        target = f"{pid}_{flat}.png"
        if base == "sleuth_merged" and any(key in _norm(alt) for key in SLEUTH_KEYS):
            warnings.append(f"其他评论者补充图 alt 疑似含打假人姓名（违反统一口径）：{alt}")
        if not dry_run:
            out.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out / target)
        return f"![{alt}]({target})"

    rewritten = IMAGE_REF_RE.sub(rewrite, text)
    if "知名打假人" in rewritten:
        warnings.append("产出含「知名打假人」字样，违反打假人署名统一口径")
    norm_text = _norm(rewritten)
    for key in SLEUTH_KEYS:
        if key in norm_text:
            warnings.append(f"产出含打假人姓名「{key}」，违反署名统一口径（正文不应出现打假人姓名）")
    if not dry_run:
        out.mkdir(parents=True, exist_ok=True)
        out_md = out / (f"{issue}.md" if issue else draft.name.replace("_draft", ""))
        warnings.extend(copy_intro(out))
        if f"]({INTRO_IMG})" not in rewritten:
            warnings.append("草稿未包含固定简介图引用（intro.png）——检查写稿提示词固定块是否被遵守")
        out_md.write_text(rewritten, encoding="utf-8")
        print(f"已写出：{out_md}")
    return warnings
