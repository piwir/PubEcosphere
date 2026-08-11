"""图材整理：从 pick 素材夹生成推文图材，每篇三类合并图（first/author/sleuth）。

用法：
    python -m scoring.material --pub-dir output/issue/-1/pub \
        --issue-dir output/issue/-1 --out output/issue/-1/material

输入是 issue.py pick 的产物（pub/<pid>_files/ 下 md 与评论图同目录），输出到
<out>/<pid>/ 的图材夹，供后续 LLM 周报生成当素材：

1. 图片路径修正：export/issue 已保证 md 与图片同在 <pid>_files/ 下，链接为裸文件名，
   本模块把选定图片与结构化 md 归到同一目录 <pid>/。
2. 多图合并，每张合并图默认至多 4 张源图（均匀网格、白底、细灰边框，避免塞太多看不清楚）；
   超限自动拆多张：第 0 张 `first_merged.png`，第 N 张 `first_merged_N.png`。
   每篇三类各自拆分，合并图总数允许超过 3：
   - first_merged[_N].png   最早提出质疑的评论者（首位质疑人）发的图；
   - author_merged[_N].png  若有作者回应，作者回应的图；
   - sleuth_merged[_N].png  若有知名打假人且与首位质疑人不同，其图再合并备用。
3. 结构化输出：<out>/index.md（清单）+ <out>/<pid>/<pid>.md（每篇素材，内嵌合并图）。

打假人判定复用 signals.matches_sleuth（精确别名匹配，名单在 config.ScoringConfig.sleuths）。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from .config import ScoringConfig
from .signals import _norm_name, matches_sleuth

_COMMENT_RE = re.compile(r"^### (\d+)\. (.*?) · (\d{4}-\d{2}-\d{2} \d{2}:\d{2})(.*)$")
_IMG_MD_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_IMG_HTML_RE = re.compile(r'<img\b[^>]*?src=["\']([^"\']+)', re.IGNORECASE)
_WHO_RE = re.compile(r"^(.*?)（(.*)）$")

_IMG_NOTE = "【图，已合并见上】"


@dataclass
class Comment:
    num: int
    alias: str
    name: str
    when: str
    is_author: bool
    body: str
    images: list[str] = field(default_factory=list)


def _split_who(who: str) -> tuple[str, str]:
    """'Elisabeth M Bik（Elisabeth M Bik）' → ('Elisabeth M Bik', 'Elisabeth M Bik')；
    无括号时（匿名评论也总带别名括号，但兜底）原样返回。"""
    m = _WHO_RE.match(who)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return who.strip(), who.strip()


def parse_comments(md: str) -> list[Comment]:
    """把一篇 pub/ 的完整评论 md 拆成结构化评论列表（含各自引用的图片文件名）。"""
    comments: list[Comment] = []
    cur: Comment | None = None
    body_lines: list[str] = []
    for line in md.splitlines():
        m = _COMMENT_RE.match(line)
        if m:
            if cur is not None:
                cur.body = "\n".join(body_lines).strip()
            alias, name = _split_who(m.group(2))
            cur = Comment(num=int(m.group(1)), alias=alias, name=name,
                          when=m.group(3), is_author="作者回应" in m.group(4), body="", images=[])
            comments.append(cur)
            body_lines = []
        elif cur is not None:
            body_lines.append(line)
    if cur is not None:
        cur.body = "\n".join(body_lines).strip()

    for c in comments:
        names = []
        for mm in _IMG_MD_RE.finditer(c.body):
            names.append(_basename(mm.group(2)))
        for mm in _IMG_HTML_RE.finditer(c.body):
            names.append(_basename(mm.group(1)))
        c.images = names
    return comments


def _basename(name: str) -> str:
    """评论里的图片引用可能是裸文件名或 <pid>_files/ 前缀，统一取 basename 再查盘。"""
    return name.strip().rsplit("/", 1)[-1]


def _dedup(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for s in seq:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _is_sleuth(c: Comment, sleuths: tuple) -> bool:
    return matches_sleuth(c.alias, sleuths) or matches_sleuth(c.name, sleuths)


def _has_identity(c: Comment) -> bool:
    """是否有可归并的署名：真别名或真名（'匿名'占位、空名不可归并）。

    库里匿名评论都带稳定的 user_name 马甲（如 'illex illecebrosus'），仍按名字归并；
    只有别名和真名都缺失/占位时视为无身份，避免这类评论被整体合并成一人。
    """
    if c.alias and c.alias != "匿名":
        return True
    return bool(c.name) and c.name != "匿名"


def _same_commenter(a: Comment, b: Comment) -> bool:
    """判断两条评论是否同一人。

    与 _is_sleuth（matches_sleuth）同用 _norm_name 归一，容忍同一人写法差异
    （'Elisabeth M Bik'/'Elisabeth M. Bik'、大小写、'Hoya Camphorifolia'/'hoya camphorifolia'），
    确保「打假人=首质疑人」时能识别为同一人、不重复生成备用图。
    匿名/无有效署名不参与归并。
    """
    if a is b:
        return True
    if not (_has_identity(a) and _has_identity(b)):
        return False
    a_alias, b_alias = _norm_name(a.alias), _norm_name(b.alias)
    if a_alias and b_alias:
        return a_alias == b_alias
    return _norm_name(a.name) == _norm_name(b.name)


def _open_rgb(path: Path) -> Image.Image:
    """打开图片并归一为 RGB（GIF 取首帧，透明背景合成到白色）。"""
    im = Image.open(path)
    if getattr(im, "format", None) == "GIF":
        im.seek(0)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        bg.alpha_composite(rgba)
        im = bg.convert("RGB")
    else:
        im = im.convert("RGB")
    return im


def _fit_box(im: Image.Image, max_w: int, max_h: int) -> Image.Image:
    """按比例缩放到 (max_w, max_h) 框内（只缩小不放大），保持纵横比。"""
    w, h = im.size
    if w > max_w or h > max_h:
        ratio = min(max_w / w, max_h / h)
        im = im.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), Image.LANCZOS)
    return im


def _numbered_path(path: Path, index: int) -> Path:
    """out_path 是第 0 张；第 index 张在 .png 前插序号：first_merged.png → first_merged_2.png。"""
    return path.with_name(f"{path.stem}_{index + 1}{path.suffix}")


def _draw_cell_border(canvas: Image.Image, x: int, y: int, w: int, h: int,
                      color: tuple = (222, 222, 222), width: int = 1) -> None:
    """在网格单元格外沿画一圈浅灰细边框，区分每张小图。"""
    d = ImageDraw.Draw(canvas)
    d.rectangle([x, y, x + w - 1, y + h - 1], outline=color, width=width)


def merge_images(paths: list[Path], out_path: Path, max_images: int = 4,
                 max_cell_w: int = 1000, max_cell_h: int = 800, gap: int = 10,
                 cols: int | None = None, border: bool = True) -> list[Path]:
    """把多张评论图合并成若干张（每张至多 max_images 张源图，均匀网格、白底、细灰边框）。

    超出 max_images 自动拆成多张：第 0 张写 out_path，第 N 张写 *_N.png
    （first_merged.png / first_merged_2.png / …）。每张源图归一化到
    (max_cell_w, max_cell_h) 框内；列数 ≤2 张单列、3 张以上两列，避免合并太多看不清楚。
    返回实际写出的路径列表（paths 为空返回 []）。
    """
    if not paths:
        return []
    batches = [paths[i:i + max_images] for i in range(0, len(paths), max_images)]
    out_paths: list[Path] = []
    for bi, batch in enumerate(batches):
        imgs = [_fit_box(_open_rgb(p), max_cell_w, max_cell_h) for p in batch]
        n = len(imgs)
        ncols = cols or (1 if n <= 2 else 2)
        nrows = (n + ncols - 1) // ncols
        cell_w = max(im.size[0] for im in imgs)
        cell_h = max(im.size[1] for im in imgs)
        canvas = Image.new("RGB",
                           (ncols * cell_w + (ncols + 1) * gap, nrows * cell_h + (nrows + 1) * gap),
                           (255, 255, 255))
        for idx, im in enumerate(imgs):
            r, c = divmod(idx, ncols)
            x = gap + c * (cell_w + gap)
            y = gap + r * (cell_h + gap)
            canvas.paste(im, (x + (cell_w - im.size[0]) // 2, y + (cell_h - im.size[1]) // 2))
            if border:
                _draw_cell_border(canvas, x, y, cell_w, cell_h)
        target = out_path if bi == 0 else _numbered_path(out_path, bi)
        target.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(target)
        out_paths.append(target)
    return out_paths


def _md_meta(md: str) -> dict:
    """从 pub 评论 md 顶部元数据提取 title/journal/doi/pubpeer_url。"""
    meta = {"title": "", "journal": "", "doi": "", "pubpeer_url": "", "url": ""}
    lines = md.splitlines()
    if lines and lines[0].startswith("# "):
        meta["title"] = lines[0][2:].strip()
    for line in lines[1:]:
        if line.startswith("- 期刊："):
            meta["journal"] = line[len("- 期刊："):].strip()
        elif line.startswith("- DOI："):
            meta["doi"] = line[len("- DOI："):].strip()
        elif line.startswith("- 链接：[PubPeer 讨论]("):
            meta["pubpeer_url"] = line[line.index("](") + 2:-1]
        elif line.startswith("- 原文：["):
            meta["url"] = line[line.index("](") + 2:-1]
    return meta


def _images_on_disk(files_dir: Path) -> dict[str, Path]:
    """<pid>_files/ 下图片文件名 → 实际文件路径（md 与图同目录）。"""
    if not files_dir.is_dir():
        return {}
    return {p.name: p for p in files_dir.iterdir() if p.is_file()}


def _comment_block(c: Comment, img_paths: dict[str, Path]) -> str:
    """渲染一条评论正文：图片引用替换为文字标记（图已合并到素材图区）。"""
    body = c.body
    for mm in _IMG_MD_RE.finditer(body):
        body = body.replace(mm.group(0), _IMG_NOTE)
    for mm in _IMG_HTML_RE.finditer(body):
        body = body.replace(mm.group(0), _IMG_NOTE)
    n_img = len(c.images)
    tag = "（作者回应）" if c.is_author else ""
    head = f"### {c.num}. {c.alias} · {c.when}{tag}  [图 {n_img} 张]"
    return f"{head}\n\n{body or '（无正文）'}\n"


def _merged_section(label: str, files: list[str], fig_note: str) -> list[str]:
    """渲染「图（推文用）」小节：多张合并图逐张列出，第 2 张起在 note 后补「（第 N 张）」。

    例如 first_merged.png → `![质疑人证据图（合并 8 张源图）](first_merged.png)`；
         first_merged_2.png → `![质疑人证据图（合并 8 张源图）（第 2 张）](first_merged_2.png)`。
    """
    lines = [f"## {label}（推文用）"]
    for i, name in enumerate(files):
        pos = "（第 %d 张）" % (i + 1) if i > 0 else ""
        lines.append(f"![{label}{fig_note}{pos}]({name})")
    if not files:
        lines.append("（无图）")
    lines.append("")
    return lines


def _fig_note(files: list[str], src_n: int) -> str:
    """合并图数量注记：'（2 张合并图，源图 8 张）'；单源单合并时简写 '（1 张）'。"""
    if not files:
        return "（无图）"
    n = len(files)
    if n == 1 and src_n == 1:
        return "（1 张）"
    return f"（{n} 张合并图，源图 {src_n} 张）"


def _paper_md(pid: str, meta: dict, first: Comment, sleuth: Comment | None,
              first_n: int, first_n_img: int, sleuth_n: int, sleuth_n_img: int,
              author_n: int, author_n_img: int,
              first_files: list[str], sleuth_files: list[str], author_files: list[str],
              first_block: str, sleuth_block: str, author_bodies: list[str]) -> str:
    pubpeer = meta.get("pubpeer_url") or f"https://pubpeer.com/publications/{pid}"
    sleuth_label = f"知名打假人：{sleuth.alias}" if sleuth else "知名打假人：无"
    first_fig = f"（合并 {first_n_img} 张源图）" if first_n_img else "（无图）"
    sleuth_fig = f"（合并 {sleuth_n_img} 张源图）" if sleuth_n_img else ""
    author_fig = f"（合并 {author_n_img} 张源图）" if author_n_img else ""
    lines = [
        f"# {meta.get('title') or pid}",
        f"- 期刊：{meta.get('journal') or '-'}",
        f"- DOI：{meta.get('doi') or '-'}",
        f"- PubPeer：[讨论]({pubpeer})",
        f"- 质疑人：{first.alias}（最早质疑 {first.when}，共 {first_n} 条评论）",
        f"- {sleuth_label}{f'（备用图 {sleuth_n_img} 张源图）' if sleuth_n_img else ''}",
        f"- 作者回应：{'是' if author_n else '否'}{f'（共 {author_n} 条评论）' if author_n else ''}",
        f"- 素材图：质疑人图{_fig_note(first_files, first_n_img)}"
        f"{f' · 打假人备用图{_fig_note(sleuth_files, sleuth_n_img)}' if sleuth_files else ''}"
        f"{f' · 作者回应图{_fig_note(author_files, author_n_img)}' if author_files else ''}",
        "",
        "## 质疑人证据图（推文用）",
    ]
    lines += _merged_section("质疑人证据图", first_files, first_fig)
    if sleuth_files:
        lines += _merged_section("知名打假人备用图", sleuth_files, sleuth_fig)
    if author_files:
        lines += _merged_section("作者回应图", author_files, author_fig)
    lines += ["## 相关评论", "", first_block or f"### 质疑人 · {first.when}\n\n（无正文）", ""]
    if sleuth_block and sleuth_files:
        lines += [sleuth_block, ""]
    for b in author_bodies:
        lines += [b, ""]
    return "\n".join(lines) + "\n"


def build_material(pub_dir: Path, out_root: Path, issue_dir: Path | None = None,
                   sleuths: tuple = ScoringConfig.sleuths, dry_run: bool = False,
                   max_images: int = 4, max_cell_w: int = 1000, max_cell_h: int = 800,
                   cols: int | None = None) -> list[dict]:
    """从 pub/ 素材夹生成图材夹。返回每篇的处理结果（含路径与图数，供 index 渲染）。"""
    pub_dir = Path(pub_dir)
    out_root = Path(out_root)

    # 可选：读 issue 的 manifest.json 补充类别/分数/期刊等展示字段
    manifest: dict = {}
    if issue_dir is not None:
        mf = Path(issue_dir) / "manifest.json"
        if mf.is_file():
            manifest = {p["pubpeer_id"]: p for p in (json.loads(mf.read_text(encoding="utf-8")).get("picks") or [])}

    results: list[dict] = []
    for md_path in sorted(pub_dir.glob("*_files/*.md")):
        pid = md_path.stem
        files_dir = md_path.parent
        md = md_path.read_text(encoding="utf-8")
        comments = parse_comments(md)
        non_author = [c for c in comments if not c.is_author]
        if not non_author:
            continue  # 无质疑者 → 无图材

        # 第一位质疑者：最早的非作者评论者（附图优先；无图则取最早）
        first = next((c for c in sorted(non_author, key=lambda c: c.when) if c.images), None) \
            or min(non_author, key=lambda c: c.when)
        first_comments = [c for c in comments if _same_commenter(c, first) and not c.is_author]
        first_imgs = _dedup([im for c in first_comments for im in c.images])

        # 知名打假人（备用图）：仅当与首位质疑人不同人时另取
        sleuth = next((c for c in comments if _is_sleuth(c, sleuths) and not c.is_author), None)
        sleuth_comments = ([c for c in comments if _same_commenter(c, sleuth) and not c.is_author]
                           if sleuth and not _same_commenter(sleuth, first) else [])
        sleuth_imgs = _dedup([im for c in sleuth_comments for im in c.images])

        author_comments = [c for c in comments if c.is_author]
        author_imgs = _dedup([im for c in author_comments for im in c.images])

        meta = _md_meta(md)
        pick = manifest.get(pid, {})
        if pick.get("category"):
            meta["category"] = pick["category"]
        if pick.get("final_score") is not None:
            meta["final_score"] = round(pick["final_score"], 3)

        result = {
            "pubpeer_id": pid, "title": meta.get("title"), "journal": meta.get("journal"),
            "category": meta.get("category"), "final_score": meta.get("final_score"),
            "questioner": first.alias, "first_at": first.when,
            "sleuth": sleuth.alias if sleuth else None,
            "first_n": len(first_comments), "first_n_img": len(first_imgs),
            "sleuth_n": len(sleuth_comments), "sleuth_n_img": len(sleuth_imgs),
            "author_n": len(author_comments), "author_n_img": len(author_imgs),
            "first_paths": [], "sleuth_paths": [], "author_paths": [], "md_rel": None,
        }

        if not dry_run:
            imgs_on_disk = _images_on_disk(files_dir)
            work_dir = out_root / pid
            work_dir.mkdir(parents=True, exist_ok=True)

            first_paths = [imgs_on_disk[n] for n in first_imgs if n in imgs_on_disk]
            result["first_paths"] = merge_images(first_paths, work_dir / "first_merged.png",
                                                 max_images=max_images, max_cell_w=max_cell_w,
                                                 max_cell_h=max_cell_h, cols=cols)
            sleuth_paths = [imgs_on_disk[n] for n in sleuth_imgs if n in imgs_on_disk]
            result["sleuth_paths"] = merge_images(sleuth_paths, work_dir / "sleuth_merged.png",
                                                  max_images=max_images, max_cell_w=max_cell_w,
                                                  max_cell_h=max_cell_h, cols=cols)
            author_paths = [imgs_on_disk[n] for n in author_imgs if n in imgs_on_disk]
            result["author_paths"] = merge_images(author_paths, work_dir / "author_merged.png",
                                                  max_images=max_images, max_cell_w=max_cell_w,
                                                  max_cell_h=max_cell_h, cols=cols)

            first_block = _comment_block(first_comments[0], imgs_on_disk) if first_comments else ""
            sleuth_block = _comment_block(sleuth_comments[0], imgs_on_disk) if sleuth_comments else ""
            author_bodies = [_comment_block(c, imgs_on_disk) for c in author_comments if c.body]
            content = _paper_md(pid, meta, first, sleuth,
                                len(first_comments), len(first_paths),
                                len(sleuth_comments), len(sleuth_paths),
                                len(author_comments), len(author_paths),
                                [p.name for p in result["first_paths"]],
                                [p.name for p in result["sleuth_paths"]],
                                [p.name for p in result["author_paths"]],
                                first_block, sleuth_block, author_bodies)
            (work_dir / f"{pid}.md").write_text(content, encoding="utf-8")
            result["md_rel"] = f"{pid}/{pid}.md"

        results.append(result)
        print(f"  {pid}: 质疑人 {first.alias} · 最早 {first.when}"
              f"{f' · 打假人 {sleuth.alias}' if sleuth else ''} · "
              f"质疑图 {len(first_imgs)}源→{len(result['first_paths'])}张 · "
              f"备用图 {len(sleuth_imgs)}源→{len(result['sleuth_paths'])}张 · "
              f"回应图 {len(author_imgs)}源→{len(result['author_paths'])}张",
              flush=True)
    return results


def _img_links(pid: str, paths: list[Path]) -> str:
    """index 里某类的合并图链接：多张合并图依次列出（first_merged.png / first_merged_2.png）。"""
    if not paths:
        return "-"
    return " ".join(f"[图]({pid}/{p.name})" for p in paths)


def _index_md(issue: str, results: list[dict]) -> str:
    with_img = [r for r in results if r["first_n_img"] or r["author_n_img"] or r["sleuth_n_img"]]
    lines = [
        f"# 周报图材 · issue {issue}",
        "",
        f"- 图材 {len(results)} 篇（含合并图 {len(with_img)} 篇）",
        "",
        "| 分类 | 标题 | 期刊 | 质疑人 | 最早质疑 | 打假人 | 作者回应 | 质疑图 | 备用图 | 回应图 | 素材 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        cat = r.get("category") or "-"
        title = (r.get("title") or r["pubpeer_id"])[:40]
        journal = (r.get("journal") or "")[:24]
        resp = f"{r['author_n']} 条" if r["author_n"] else "否"
        sleuth = r.get("sleuth") or "-"
        first_img = _img_links(r["pubpeer_id"], r["first_paths"])
        sleuth_img = _img_links(r["pubpeer_id"], r["sleuth_paths"])
        author_img = _img_links(r["pubpeer_id"], r["author_paths"])
        link = f"[md]({r['md_rel']})" if r["md_rel"] else "-"
        lines.append(f"| {cat} | {title} | {journal} | {r['questioner']} | {r['first_at']} | {sleuth} | "
                     f"{resp} | {first_img} | {sleuth_img} | {author_img} | {link} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PubEcosphere 图材整理：质疑人/作者/打假人图合并 + 结构化 md")
    ap.add_argument("--pub-dir", default="output/issue/-1/pub", help="pick 素材夹（含 <pid>_files/ md+图片）")
    ap.add_argument("--issue-dir", default=None, help="issue 目录（读 manifest.json 补分类/分数），默认取 pub-dir 上级")
    ap.add_argument("--out", default="output/issue/-1/material", help="图材输出目录")
    ap.add_argument("--max-images", type=int, default=4, help="每张合并图最多合并的源图数，超限自动拆 *_2.png 等")
    ap.add_argument("--max-cell-w", type=int, default=1000, help="合并图中单张源图最大宽度（px）")
    ap.add_argument("--max-cell-h", type=int, default=800, help="合并图中单张源图最大高度（px）")
    ap.add_argument("--cols", type=int, default=None, help="合并图网格列数（默认 ≤2 张单列、3 张以上两列）")
    ap.add_argument("--dry-run", action="store_true", help="只看会输出哪些篇，不写文件")
    args = ap.parse_args(argv)

    issue_dir = Path(args.issue_dir) if args.issue_dir else Path(args.pub_dir).parent
    issue = issue_dir.name
    out_root = Path(args.out)

    if not args.dry_run:
        # 整体重建：清掉历史残留合并图再重跑（原为 CLAUDE.md 手工删除约定，现代码化）
        shutil.rmtree(out_root, ignore_errors=True)
        out_root.mkdir(parents=True, exist_ok=True)

    cfg = ScoringConfig()
    results = build_material(Path(args.pub_dir), out_root, issue_dir=issue_dir,
                             sleuths=cfg.sleuths, dry_run=args.dry_run,
                             max_images=args.max_images, max_cell_w=args.max_cell_w,
                             max_cell_h=args.max_cell_h, cols=args.cols)
    if not args.dry_run:
        (out_root / "index.md").write_text(_index_md(issue, results), encoding="utf-8")
    n_img = sum(1 for r in results if r["first_paths"] or r["sleuth_paths"] or r["author_paths"])
    suffix = f" → {out_root}" if not args.dry_run else "（dry-run）"
    print(f"material done: {len(results)} papers with material, {n_img} with merged images{suffix}",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
