"""LLM 周报生成包：单模型端到端（`python -m llm generate`：同一模型完成提取 + 写稿）。

纯文本路径（DeepSeek 无多模态，图片描述来自评论者配图时写下的原话）；
OpenAI 兼容 `chat/completions`，纯 stdlib 实现，无第三方依赖。
`python -m scoring.pipeline` 的任何子命令都不会 import 本包。

CLI 入口见 `llm/__main__.py`（check / selftest / assemble / generate / md2html）；
离线自测见 `llm/selftest.py`；方案细节见 `docs/llm-scheme.md`。
"""
