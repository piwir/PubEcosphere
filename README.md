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
# 环境
conda env create -f environment.yml && conda activate pubecosphere

# 1. 每日捕获 feed（服务器用 cron 常驻）
python -m crawler.crawl --db data/pubpeer.db capture

# 2. ≥7 天后回访文章页，提取完整评论线程
python -m crawler.crawl --db data/pubpeer.db revisit --limit 50

# 3. 导出存档：每篇 md 与评论图片同目录（output/pub/<pid>_files/）
python -m crawler.export --db data/pubpeer.db --output output

# 4. 两阶段打分（--stage1-only 只粗筛；--include-published 强制含已发布）
python -m scoring.pipeline rank --db data/pubpeer.db

# 5. 每类选篇打包本期素材（--dry-run 先看选什么；测试期用 --issue=-1）
python -m scoring.pipeline pick --db data/pubpeer.db --issue=-1 --dry-run
python -m scoring.pipeline pick --db data/pubpeer.db --issue=-1

# 6. 图材合并（每篇至多 3 张合并图：first / author / sleuth）
python -m scoring.material --pub-dir output/issue/-1/pub --out output/issue/-1/material
```

## LLM 周报生成

当前**手动上传**（贴提示词 + 上传扁平素材），`llm/` 为预留 API 接入、默认不启用。方案与提示词见 `docs/llm-scheme.md` 与 `docs/prompts/`（提示词随仓库提交）。

```bash
# 摊平上传文件夹（无子目录，方便对话平台框选上传）
python -m llm flatten --material-dir output/issue/-1/material \
    --upload-dir output/issue/-1/upload --manifest output/issue/-1/manifest.json

# 排版：草稿 → 成品 md + 图复制（md 与图同目录，供微信 HTML 转换）
python -m llm assemble --material-dir output/issue/-1/material \
    --weekly-dir output/issue/-1/weekly --issue -1

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
| `docs/prompts/` | 多模态提取 + 写稿 两套提示词 |


## 免责声明

1. 本项目仅供学术诚信研究与交流使用，所有数据来源于 PubPeer 公开评论。
2. 本项目不对因使用本系统造成的任何后果负责。
