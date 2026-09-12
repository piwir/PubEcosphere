"""架构思维导图：解析 codegraph.txt 的「## 文件树」段 → Pillow 绘制两级左→右思维导图。

- 两级展开：根框（项目名）→ 一级顶层目录（代码模块 vs 工程支撑折叠行）→ 模块框内列二级子目录。
- 仅依赖 Pillow + DejaVu 字体，标签全 ASCII；任一条件不满足返回 None 并告警，不阻塞抓取/写稿。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ARCH_FILENAME = "arch.png"
ARCH_ALT = "代码架构图"

MAX_MODULES = 10   # 一级代码模块展示上限（溢出折叠为「+N more」）
MAX_CHILDREN = 8   # 每个模块展示的二级子目录上限（画布超高时自动减半）
MAX_HEIGHT = 5000  # 画布高度上限（px）

# 工程支撑目录：非运行时代码，折叠为一行次要文字
SUPPORT_DIRS = frozenset({
    ".github", "conda", "readthedocs", "scripts", "tests", "docs", "examples",
    "benchmarks", "ci", "docker", "paper", "assets", "deploy", "notebooks"})

# 配色取站点六色 token（src/site/src/styles/main.css），不引入表外色
PAPER = "#FBFAF6"
INK = "#16181D"
HIGHLIGHT = "#FFD84D"
MUTED = "#6F6A5E"
LINE = "#E6E2D6"

_TREE_HEADER = "## 文件树"
_LINE_RE = re.compile(r"^((?:│   |    )*)(?:├── |└── )(.*)$")
_FILE_RE = re.compile(r"^(.*) \([a-zA-Z0-9_+#.-]+, \d+ symbols\)$")
_FILES_RE = re.compile(r"Project Structure \((\d+) files\)")


@dataclass
class _Node:
    name: str
    children: dict[str, "_Node"] = field(default_factory=dict)
    is_file: bool = False


def parse_tree(text: str) -> _Node | None:
    """解析「## 文件树」段为树；无该段或空树返回 None。"""
    start = text.find(_TREE_HEADER)
    if start < 0:
        return None
    root = _Node(name="")
    stack: list[tuple[int, _Node]] = [(-1, root)]
    started = False
    for raw in text[start + len(_TREE_HEADER):].splitlines():
        m = _LINE_RE.match(raw.rstrip())
        if not m:
            if started:
                break  # 段落结束（下一个小节标题或文本截断）
            continue   # 段头说明行（Project Structure (N files): 等）
        started = True
        depth = len(m.group(1)) // 4
        label = m.group(2)
        while len(stack) > 1 and stack[-1][0] >= depth:
            stack.pop()
        node = _Node(name=label, is_file=_FILE_RE.match(label) is not None)
        stack[-1][1].children[label] = node
        stack.append((depth, node))
    return root if root.children else None


def _classify(root: _Node) -> tuple[list[_Node], list[str]]:
    """顶层条目 → (代码模块，按子项数降序，工程支撑名单)。顶层散文件归支撑。"""
    code: list[_Node] = []
    support: list[str] = []
    for name, node in root.children.items():
        if node.is_file or name in SUPPORT_DIRS or name.startswith("."):
            support.append(_child_label(name, node.is_file))
        else:
            code.append(node)
    code.sort(key=lambda n: -len(n.children))
    return code, support


def _find_font(names: list[str]) -> str | None:
    env = os.environ.get("PUBAI4S_FONT")
    if env and Path(env).exists():
        return env
    for d in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/TTF"):
        for n in names:
            p = Path(d) / n
            if p.exists():
                return str(p)
    return None


def _child_label(name: str, is_file: bool) -> str:
    if is_file:
        m = _FILE_RE.match(name)
        return m.group(1) if m else name
    return name


def generate_arch_png(codegraph_text: str, out_path: Path, project: str) -> Path | None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("警告（archmap）：Pillow 未安装，跳过架构图生成")
        return None

    root = parse_tree(codegraph_text)
    if root is None:
        print("警告（archmap）：codegraph.txt 无「## 文件树」段或解析失败，跳过架构图生成")
        return None
    modules, support = _classify(root)
    if not modules:
        print("警告（archmap）：文件树中未识别出代码模块，跳过架构图生成")
        return None
    reg_path = _find_font(["DejaVuSans.ttf"])
    bold_path = _find_font(["DejaVuSans-Bold.ttf"]) or reg_path
    if reg_path is None:
        print("警告（archmap）：未找到 DejaVu 字体（可设 PUBAI4S_FONT 指定 TTF），跳过架构图生成")
        return None

    f_root = ImageFont.truetype(bold_path, 40)
    f_sub = ImageFont.truetype(reg_path, 24)
    f_mod = ImageFont.truetype(bold_path, 30)
    f_child = ImageFont.truetype(reg_path, 22)
    f_foot = ImageFont.truetype(reg_path, 18)

    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))

    def w_of(s: str, f: ImageFont.FreeTypeFont) -> int:
        b = probe.textbbox((0, 0), s, font=f)
        return b[2] - b[0]

    PAD_X, PAD_Y = 30, 24
    NAME_H = 46   # 模块名行高（含荧光条）
    CHILD_H = 34  # 子项行高
    GAP = 22      # 模块框间距
    LINK_AREA = 150
    MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 70, 64, 56

    def build(max_children: int):
        boxes = []
        for m in modules[:MAX_MODULES]:
            kids = [_child_label(n, node.is_file) for n, node in m.children.items()]
            shown = kids[:max_children]
            over = len(kids) - len(shown)
            lines = shown + ([f"+{over} more"] if over > 0 else [])
            w = max([w_of(m.name, f_mod)] + [w_of("·  " + k, f_child) for k in lines],
                    default=0) + PAD_X * 2
            h = PAD_Y * 2 + NAME_H + len(lines) * CHILD_H
            boxes.append((m.name, lines, w, h))
        return boxes

    max_children = MAX_CHILDREN
    while True:
        boxes = build(max_children)
        body_h = sum(b[3] for b in boxes) + GAP * (len(boxes) - 1)
        height = (MARGIN_TOP + body_h
                  + ((CHILD_H + 18) if support else 0)
                  + MARGIN_BOTTOM + 44)
        if height <= MAX_HEIGHT or max_children <= 2:
            break
        max_children //= 2

    support_text = ""
    if support:
        shown = support[:6]
        tail = f" (+{len(support) - len(shown)})" if len(support) > 6 else ""
        support_text = "support: " + " · ".join(shown) + tail

    n_files = _FILES_RE.search(codegraph_text)
    sub = (f"Project Structure · {n_files.group(1)} files" if n_files
           else "Project Structure")
    root_w = max(w_of(project, f_root), w_of(sub, f_sub)) + 88
    root_h = 150
    col_w = max([b[2] for b in boxes] + [w_of(support_text, f_child) if support_text else 0,
                                         420])
    width = MARGIN_X + root_w + LINK_AREA + col_w + MARGIN_X

    img = Image.new("RGB", (width, height), PAPER)
    d = ImageDraw.Draw(img)

    root_cy = height // 2
    rx0 = MARGIN_X
    ry0 = root_cy - root_h // 2
    d.rounded_rectangle([rx0, ry0, rx0 + root_w, ry0 + root_h], radius=18, fill=INK)
    d.text((rx0 + 44, ry0 + 32), project, font=f_root, fill=PAPER)
    d.text((rx0 + 44, ry0 + 92), sub, font=f_sub, fill=LINE)

    col_x = MARGIN_X + root_w + LINK_AREA
    y = MARGIN_TOP
    for name, lines, w, h in boxes:
        cy = y + h // 2
        d.line([(rx0 + root_w, root_cy), (col_x, cy)], fill=MUTED, width=2)
        d.ellipse([col_x - 4, cy - 4, col_x + 4, cy + 4], fill=MUTED)
        d.rounded_rectangle([col_x, y, col_x + w, y + h], radius=14,
                            fill="#FFFFFF", outline=LINE, width=2)
        d.text((col_x + PAD_X, y + PAD_Y - 4), name, font=f_mod, fill=INK)
        name_w = w_of(name, f_mod)
        d.rounded_rectangle(
            [col_x + PAD_X, y + PAD_Y + 34, col_x + PAD_X + name_w, y + PAD_Y + 42],
            radius=3, fill=HIGHLIGHT)
        ty = y + PAD_Y + NAME_H
        for k in lines:
            d.text((col_x + PAD_X, ty), "·  " + k, font=f_child, fill=MUTED)
            ty += CHILD_H
        y += h + GAP

    if support_text:
        d.text((col_x, y), support_text, font=f_child, fill=MUTED)

    footer = "Architecture overview · generated from codegraph"
    d.text((width - MARGIN_X - w_of(footer, f_foot), height - 40), footer,
           font=f_foot, fill=MUTED)

    img.save(out_path, "PNG")
    print(f"架构图已生成：{out_path}（{width}x{height}，{len(modules)} 个顶层代码模块，"
          f"展示 {len(boxes)} 个）")
    return out_path


def ensure_arch_png(out_dir: Path, project: str) -> Path | None:
    """从 out_dir/inputs/codegraph.txt 生成 <out_dir>/arch.png（缺输入则静默跳过）。"""
    cg = out_dir / "inputs" / "codegraph.txt"
    if not cg.exists():
        return None
    return generate_arch_png(cg.read_text(encoding="utf-8"),
                             out_dir / ARCH_FILENAME, project)


def prepend_arch_manifest(images_md: Path) -> None:
    """images.md 存在且尚无 arch 行时，把 arch.png 插到清单首位。"""
    if not images_md.exists():
        return
    line = f"- ![{ARCH_ALT}]({ARCH_FILENAME})"
    old = images_md.read_text(encoding="utf-8")
    if old.startswith(line) or f"\n{line}\n" in f"\n{old}":
        return
    images_md.write_text(line + "\n" + old, encoding="utf-8")
