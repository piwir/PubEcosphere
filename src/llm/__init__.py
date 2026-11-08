"""通用 LLM 与渲染层：两条产品线共用，**不含周报业务逻辑**。

组成：
    client       OpenAI 兼容 `chat/completions` 客户端（纯 stdlib，无第三方依赖；可注入 transport）
    config        模型/端点/密钥配置（只读环境变量与仓库根 `.env`，密钥绝不硬编码）
    md2html       md → 微信兼容 HTML（仓库内 vendored 转换器，bun 运行时）
    inline_images 图片 base64 内联（微信手动粘贴专用）

依赖方向：`modules/weekly` 与 `modules/ai4s` 都可以 import 本包；
本包不 import 任何产品线（不 import crawler / scoring / writer / pubai4s）。
周报写稿在 `modules/weekly/writer`（`python -m writer generate|assemble|…`）。
"""
from __future__ import annotations

__all__ = ["client", "config", "md2html", "inline_images"]
