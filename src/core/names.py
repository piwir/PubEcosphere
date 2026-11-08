"""知名打假人：**唯一名单与唯一匹配口径**。

名单存「真名 / 公认证实马甲」的**精确键**（小写）。判定只做书写归一 + 整串全等，
**绝不做姓氏模糊**——`Uri Ben-David` 撞 `Sholto David` 这类误报会直接毁掉周报可信度。

历史教训：这份逻辑此前散在三处（`scoring/config.py` 名单、`scoring/signals.py`
归一匹配、`llm/assemble.py` 告警扫描），改名单必须记得同步三处。现在只有一处：

    SLEUTHS              名单
    norm_name(s)         书写归一（小写 + 非字母数字折叠成单空格 + 去首尾）
    matches_sleuth(s)    单个名字是否打假人（整串全等）
    any_sleuth(names)    一组名字里是否有打假人（stage-1 用逗号串拆分后调它）
    find_sleuth(text)    长文本里是否出现打假人姓名（产出口径告警用）
"""
from __future__ import annotations

import re

# 名单（小写精确键）。新增成员只改这里。
SLEUTHS: tuple[str, ...] = (
    "elisabeth m bik",
    "hoya camphorifolia",
    "sholto david",
    "leonid schneider",
    "matthew schrag",
    "david sanders",
    "clare francis",
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def norm_name(s: str | None) -> str:
    """名字归一：小写 + 非字母数字（标点/连字符/空白/全角）折叠为单个空格 + 去首尾。

    容忍 `Elisabeth M. Bik` / `Elisabeth M Bik`、U+2010 连字符、多空格、大小写差异；
    归一后仍然要求**整串相等**，因此不做模糊匹配也不会漏掉上面这些写法。
    """
    return _NON_ALNUM.sub(" ", (s or "").strip().lower()).strip()


_NORMALIZED: tuple[str, ...] = tuple(norm_name(s) for s in SLEUTHS)


def matches_sleuth(name: str | None, sleuths: tuple[str, ...] | None = None) -> bool:
    """单个显示名（别名或真名）是否为知名打假人。

    `sleuths` 缺省用 `SLEUTHS`；传入自定义名单时先归一化再比（便于测试/配置覆盖）。
    """
    n = norm_name(name)
    if not n:
        return False
    if sleuths is None:
        return n in _NORMALIZED
    return any(norm_name(s) == n for s in sleuths)


def any_sleuth(names: list[str] | tuple[str, ...] | None,
               sleuths: tuple[str, ...] | None = None) -> bool:
    """一组名字中是否有打假人（空/None 返回 False）。"""
    return any(matches_sleuth(name, sleuths) for name in (names or ()))


def find_sleuth(text: str, sleuths: tuple[str, ...] | None = None) -> list[str]:
    """在长文本中找出出现的打假人姓名（归一后子串匹配），返回命中的规范键列表。

    仅用于**产出守门告警**（周报正文不应出现打假人姓名），不参与打分判定。
    """
    haystack = f" {norm_name(text)} "
    keys = SLEUTHS if sleuths is None else tuple(norm_name(s) for s in sleuths)
    return [k for k in keys if f" {k} " in haystack]
