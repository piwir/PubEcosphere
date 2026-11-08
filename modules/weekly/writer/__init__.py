"""周报写稿模块：素材 md → 提取 → 写稿 → 排版（周报产品线的 LLM 环节）。

包内分工：
    generate     单模型端到端：阶段A 文本提取 + 阶段B 写稿（走 .env 模型，无头/定时用）
    assemble     排版：草稿 → 成品 md + 图片复制（离线，确定性）
    polish_html  HTML 后处理：数学上下标 / 页脚小字 / GitHub 链接修复（离线、幂等）
    manifest     素材 manifest 读取与元数据补行（generate 与 assemble 共用）
    prompts      本模块自持的两份提示词加载与八节校验（核心校验在 core.prompts）
    selftest     离线自测（假 transport，不联网）

本机首选流程不调本包的 generate：由 agent 直接按提示词写稿，只有无头/定时才走 API。
通用 LLM 客户端与 md→html 渲染在 `src/llm/`（crawler/scoring 不 import 它，反之亦然）。
"""
from __future__ import annotations

__all__ = ["assemble", "generate", "manifest", "polish_html", "prompts"]
