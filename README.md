# PubEcosphere

> 一个 PubPeer 周报生成项目

基于 PubPeer（pubpeer.com）公开评论，抓取存在**作者与打假人激烈交锋**的论文，多维度打分排序，由大模型生成周报，经人工审核后发布。

## 项目简介

学术打假是维护科研诚信的重要防线。PubPeer 是全球最大的学术评论平台之一，评论区常上演作者申辩与质疑者对线的交锋。本系统：

- **自动监控** PubPeer 高关注论文的评论动态
- **智能打分** 筛选值得报道的「激烈交锋」案例
- **周报化** 由大模型生成 HelloGitHub 式周报
- **合规推送** 经人工审核后发布

## 核心工作流

```text
capture（每日捕获 feed）→ revisit（≥7 天后回访评论线程）→ export（md + 评论图本地化）
→ rank（两阶段打分选优）→ pick（每类选篇打包）→ material（图材合并）
→ LLM 生成周报 → 人工审核 → 推送
```



## 快速开始

```bash
conda activate pubecosphere

# 1. 每日捕获 feed（服务器用 cron 常驻）
python -m crawler.crawl --db data/pubpeer.db capture

# 2. ≥7 天后回访文章页，提取完整评论线程
python -m crawler.crawl --db data/pubpeer.db revisit --limit 50

# 3. 导出存档：每篇 md 与评论图片同目录（output/pub/<pid>_files/）
python -m crawler.export --db data/pubpeer.db --output output

# 4. 两阶段打分（--stage1-only 只粗筛；--include-published 强制含已发布）
#    --issue 把报告写到本期 score/；--window/--window-field 按日期基准过滤候选
python -m scoring.pipeline rank --db data/pubpeer.db --issue 1 --window 10 3 --window-field captured_at

# 5. 每类选篇打包本期素材（--dry-run 先看选什么，确认分数线后正式落）
python -m scoring.pipeline pick --db data/pubpeer.db --issue 1 --dry-run
python -m scoring.pipeline pick --db data/pubpeer.db --issue 1

# 6. 图材合并（每张合并图至多 4 张源图，超限自动拆 first_merged_2.png 等）
python -m scoring.material --pub-dir output/issue/1/pub --out output/issue/1/material
```

## LLM 周报生成

`llm/` 提供两条路径，均走 OpenAI 兼容 `chat/completions`（默认 DeepSeek）：

- **单模型路径（推荐）**：`python -m llm generate` 用**同一个模型**完成提取 + 写稿（无多模态，图片描述来自评论者配图时的原话）。
- **多模态双模型路径（预留）**：vision 提取 + writer 写稿，需要多模态模型。

提示词见 `docs/prompts/`（随仓库提交）；方案细节见 `docs/llm-scheme.md`。

### API 配置

密钥只走环境变量 / 仓库根 `.env`（**绝不硬编码、不提交**）。把 `.env.example` 复制为 `.env` 填写：

```bash
cp .env.example .env        # 编辑填入 PUBECOSPHERE_LLM_API_KEY=sk-…
```

单模型路径：`PUBECOSPHERE_LLM_MODEL`（默认 deepseek-v4-flash）、`PUBECOSPHERE_LLM_MAX_TOKENS`（默认顶满模型上限，不人为限流）。

```bash
# 摊平上传文件夹（无子目录，方便对话平台框选上传）
python -m llm flatten --material-dir output/issue/1/material \
    --upload-dir output/issue/1/upload --manifest output/issue/1/manifest.json

# 单模型端到端：提取 + 写稿（同一模型；--dry-run 只打印消息不联网；--assemble 顺带排版）
python -m llm generate --material-dir output/issue/1/material \
    --weekly-dir output/issue/1/weekly --issue 1

# 排版：草稿 → 成品 md + 图复制（md 与图同目录，供微信 HTML 转换）
python -m llm assemble --material-dir output/issue/1/material \
    --weekly-dir output/issue/1/weekly --issue 1

# 自检（离线，不联网不调模型）
python -m llm check && python -m llm selftest
```

## 项目结构

```
PubEcosphere/
├── crawler/            # 爬虫模块
├── scoring/            # 打分系统
├── llm/                # LLM 周报生成
├── images/             # 项目素材
├── data/               # 运行数据
├── output/             # 打分报告 / 素材 / 周报
├── docs/               # 文档
└── .gitignore
```

## 详细文档

| 文档 | 内容 |
| --- | --- |
| `docs/prompts/` | 多模态提取 + 文本提取 + 写稿 三套提示词 |


## 免责声明

1. 本项目仅供学术诚信研究与交流使用，所有数据来源于 PubPeer 公开评论。
2. 本项目不对因使用本系统造成的任何后果负责。
