"""codegraph 集成：下载仓库 tarball → 本地索引 → 生成代码结构摘要（写 inputs/codegraph.txt）。

依赖可选：`npm install -g @colbymchenry/codegraph`（纯本地 Rust 内核，不调 LLM）。
未安装或任一步骤失败 → 告警降级返回 ("", [警告])，不影响主流程。

实测（v1.6.0）：`init --yes` 已默认建图索引（--index flag 已废弃）；
`files --max-depth N` 输出文本树；`query <词>` 输出符号列表；
`context <任务> --no-code` 输出入口与相关符号的关系摘要（explore 会转储整段源码，体积过大不用）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

CODEGRAPH_BIN = os.environ.get("CODEGRAPH_BIN", "codegraph")  # 显式指路径可覆盖
TARBALL_LIMIT = 100 * 1024 * 1024
DOWNLOAD_READ_TIMEOUT = 120.0  # 单次 socket 读超时，防真卡死；不设总时限（大小上限即最坏上界）
INIT_TIMEOUT = 600.0   # init 含首次索引，是唯一慢命令（大仓库放宽）
CMD_TIMEOUT = 60.0     # files / query / context 共用
SUMMARY_LIMIT = 20000  # codegraph.txt 字符上限（与 README_LIMIT 同级）
PART_LIMITS = {"tree": 6000, "symbols": 4000, "graph": 4000}
NOISE_DIRS = {"docs", "examples", "tests", "test", "benchmarks", "scripts", ".github"}


def _run(cmd: list[str], cwd: Path, timeout: float) -> tuple[int, str, str]:
    """跑 codegraph 子命令；(rc, stdout, stderr)。FileNotFound→(127) 超时→(124)，不抛异常。"""
    env = {**os.environ, "CODEGRAPH_NO_DAEMON": "1", "CODEGRAPH_TELEMETRY": "0"}
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True,
                              text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, "", "codegraph 未安装"
    except subprocess.TimeoutExpired:
        return 124, "", f"超时（{timeout:.0f}s）"
    return proc.returncode, proc.stdout, proc.stderr


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n…（超出截断）"


def _download_tarball(owner: str, name: str, branch: str, dest: Path,
                      limit: int = TARBALL_LIMIT,
                      read_timeout: float = DOWNLOAD_READ_TIMEOUT) -> bool:
    """流式下载 codeload tarball 到 dest；超限/失败返回 False。

    codeload 非 API 不限速；每次 read 单独超时（网络慢时数据持续到达即可继续，
    不设总时限）。下载开始打一行提示，避免长静默被误判卡死。
    """
    url = f"https://codeload.github.com/{owner}/{name}/tar.gz/refs/heads/{branch}"
    print(f"提示（codegraph）：下载 {owner}/{name} tarball（上限 {limit // (1024 * 1024)}MB，"
          f"网络慢时需数分钟）…", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "pubai4s/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=read_timeout) as resp, open(dest, "wb") as fh:
            total = 0
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    return True
                total += len(chunk)
                if total > limit:
                    return False
                fh.write(chunk)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return False


def _safe_extract(tarball: Path, dest: Path) -> None:
    """安全解压：只解普通文件/目录，跳过链接与设备成员，拒绝绝对路径与 `..`（3.10 无 filter=）。"""
    with tarfile.open(tarball, "r:gz") as tf:
        for member in tf.getmembers():
            if member.type not in (tarfile.REGTYPE, tarfile.DIRTYPE):
                continue
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise tarfile.TarError(f"不安全成员名：{member.name}")
            tf.extract(member, dest)


def _repo_root(work: Path) -> Path:
    """codeload 顶层目录名是 <name>-<branch>（分支可含 `/`，不猜名）：取唯一顶层目录。"""
    dirs = [p for p in work.iterdir() if p.is_dir()]
    if len(dirs) != 1:
        raise RuntimeError(f"tarball 顶层目录数异常：{len(dirs)}")
    return dirs[0]


def _source_dir(root: Path) -> str:
    """挑一个顶层源码目录名做 query 词：优先 src/lib，否则第一个非噪音目录。"""
    dirs = sorted(p.name for p in root.iterdir()
                  if p.is_dir() and not p.name.startswith("."))
    for preferred in ("src", "lib", "python"):
        if preferred in dirs:
            return preferred
    for d in dirs:
        if d not in NOISE_DIRS:
            return d
    return dirs[0] if dirs else ""


def _summary_inner(work: Path, owner: str, name: str, branch: str,
                   summary_limit: int) -> tuple[str, list[str]]:
    warnings: list[str] = []
    try:
        tarball = work / "repo.tar.gz"
        if not _download_tarball(owner, name, branch, tarball):
            return "", [f"tarball 下载失败（>100MB 或网络错误）：{owner}/{name}"]
        _safe_extract(tarball, work)
        root = _repo_root(work)
    except (tarfile.TarError, OSError, RuntimeError) as exc:
        return "", [f"codegraph 下载/解压失败：{exc}"]

    rc, _, err = _run([CODEGRAPH_BIN, "init", "--yes"], root, INIT_TIMEOUT)
    if rc != 0:
        return "", [f"codegraph init 失败（rc={rc}）：{err.strip()[:200] or '未知错误'}"]

    parts: dict[str, str] = {}
    failed: list[str] = []

    rc, out, _ = _run([CODEGRAPH_BIN, "files", "--max-depth", "2"], root, CMD_TIMEOUT)
    if rc == 0 and out.strip():
        parts["tree"] = _truncate(out.strip(), PART_LIMITS["tree"])
    else:
        failed.append("files")

    symbols: list[str] = []
    for term, limit in ((name, 15), (_source_dir(root), 10)):
        if not term:
            continue
        rc, out, _ = _run([CODEGRAPH_BIN, "query", term, "--limit", str(limit)],
                          root, CMD_TIMEOUT)
        if rc == 0 and out.strip():
            symbols.append(out.strip())
    if symbols:
        parts["symbols"] = _truncate("\n\n".join(symbols), PART_LIMITS["symbols"])
    else:
        failed.append("query")

    rc, out, _ = _run([CODEGRAPH_BIN, "context",
                       f"{name}: overall architecture, main entry points and core modules",
                       "--no-code", "--max-nodes", "20"], root, CMD_TIMEOUT)
    if rc == 0 and out.strip():
        parts["graph"] = _truncate(out.strip(), PART_LIMITS["graph"])
    else:
        failed.append("context")

    sections: list[str] = []
    if parts.get("tree"):
        sections.append("## 文件树\n\n" + parts["tree"])
    if parts.get("symbols"):
        sections.append("## 关键符号\n\n" + parts["symbols"])
    if parts.get("graph"):
        sections.append("## 代码关系（入口与相关符号）\n\n" + parts["graph"])
    if failed:
        sections.append("## 说明\n\n以下命令失败未产出：" + "、".join(failed))
        warnings.append(f"codegraph 部分命令失败：{'、'.join(failed)}")
    if not sections:
        warnings.append("codegraph 无可用输出")
        return "", warnings
    return _truncate("\n\n".join(sections), summary_limit), warnings


def generate_codegraph_summary(owner: str, name: str, branch: str,
                               summary_limit: int = SUMMARY_LIMIT) -> tuple[str, list[str]]:
    """下载 tarball → 解压 → init 建图 → files/query/context → (摘要文本, 警告列表)。

    整体失败（二进制缺失/下载/解压/init）→ ("", [警告])；
    后段单条命令失败 → 保留已收集部分，摘要末尾追加「## 说明」。
    """
    if not shutil.which(CODEGRAPH_BIN):
        return "", [f"codegraph 未找到（{CODEGRAPH_BIN}）。可 `npm install -g @colbymchenry/codegraph` 或设 CODEGRAPH_BIN"]
    if not (branch or "").strip():
        return "", ["默认分支为空，跳过 codegraph"]
    work = Path(tempfile.mkdtemp(prefix="pubai4s-codegraph-"))
    try:
        return _summary_inner(work, owner, name, branch, summary_limit)
    finally:
        shutil.rmtree(work, ignore_errors=True)
