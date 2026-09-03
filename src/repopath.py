"""仓库根定位（src 布局下唯一例外）。

所有需要仓库根的模块统一从这里取，避免各自 `Path(__file__).parent.parent` 推导
（src/ 布局下那会指向 src/ 而不是仓库根）。
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
