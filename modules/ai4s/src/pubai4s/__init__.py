"""PubAI4S：把 GitHub AI4S 项目转成可发布短帖。

两段式（与父仓库同骨架）：
- 阶段A：抓取仓库元数据 + README + 官网（如有），下载图片，LLM 提取结构化材料 md；
- 阶段B：LLM 依据材料 md 写发布文案；
- 渲染：md → html → base64 内联 html（与父仓库微信流程一致的成品形态）。

输入 GitHub 仓库 URL（https / ssh / owner-repo 三种写法）。本包复用父仓库
PubEcosphere 的 `llm.*`（editable install 提供），自身零第三方依赖。
"""
__version__ = "0.1.0"
