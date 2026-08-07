"""LLM 周报生成接入包（预留未启用）。

当前主流程为「手动上传」：把 `docs/prompts/prompt_vision_extract.md` / `prompt_weekly_writer.md`
喂给多模态模型 + 上传 `output/issue/<n>/upload/` 的扁平素材，详见 `docs/llm-scheme.md`。

本包把同一套流程做成可编程调用（OpenAI 兼容 `chat/completions`），**默认不接入
scoring 主流程**，`python -m scoring.pipeline` 的任何子命令都不会 import 本包。
想启用 API 时，设置 `PUBECOSPHERE_LLM_*` 环境变量后运行 `python -m llm vision|writer`。

CLI 入口见 `llm/__main__.py`；离线自测见 `llm/selftest.py`。
"""
