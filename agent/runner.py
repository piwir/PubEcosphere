"""子进程执行：list-arg subprocess（绝不 shell=True），仓库根自推导。

挂载时无需指定 cwd：repo_root = 本文件上级的上级。所有 CLI 都在仓库根下跑，
保证 data/、output/、run_issue.sh 相对路径正确。
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class RunResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        """返回给 MCP 的合并文本：退出码 + stdout + stderr + 备注。"""
        parts: list[str] = []
        if self.stdout.strip():
            parts.append(self.stdout.rstrip())
        if self.stderr.strip():
            parts.append("[stderr]\n" + self.stderr.rstrip())
        if self.notes:
            parts.append("——\n" + "\n".join(self.notes))
        parts.append(f"[退出码 {self.exit_code}]")
        return "\n".join(parts)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def run_cli(argv: list[str], cwd: Path | None = None,
            env: dict[str, str] | None = None, timeout: int | None = None) -> RunResult:
    """以仓库根为 cwd 跑一条 CLI（list 参数，不经过 shell）。"""
    merged = dict(os.environ)
    if env:
        merged.update(env)
    try:
        proc = subprocess.run(
            argv, cwd=str(cwd or REPO_ROOT), env=merged,
            capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        return RunResult(exit_code=127, stderr=f"找不到命令：{exc}")
    except subprocess.TimeoutExpired as exc:
        return RunResult(exit_code=124, stderr=f"超时：{exc}")
    return RunResult(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
