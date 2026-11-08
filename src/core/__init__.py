"""通用层：跨模块共享的确定性小工具，不依赖任何具体产品线。

设计原则：
- **不 import** `crawler` / `scoring` / `llm` / `writer` / `pubai4s` / `agent`
  （依赖方向永远是 `modules/* → core`，反向禁止），避免循环依赖；
- 只放「唯一事实来源」的东西：同一份逻辑此前在多个包里各写一遍的，收敛到这里；
- 纯函数优先，无网络、无全局副作用（`core.prompts` 只读文件）。

模块：
    issue    期号 ↔ 素材窗口 / 发布日 / 导语标签（唯一锚点 WEEK_START）
    time     UTC 时间与 ISO 字符串
    names    知名打假人名单与名字归一/匹配
    display  期刊影响力的统一展示格式（IF / 分区 / CCF）
    db       SQLite 连接与事务（两个 Store 共享连接参数，不共享表结构）
    prompts  提示词八节骨架与校验
"""
from __future__ import annotations

__all__ = ["issue", "time", "names", "display", "db", "prompts"]
