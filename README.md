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
capture（每日捕获 feed）→ revisit（≥7 天后回访评论线程）→ rank（两阶段打分选优）
→ pick（每类选篇打包）→ material（图材合并）→ LLM 生成周报 → 人工审核 → 推送
```

## 快速开始

前置：Python 3 环境（`conda activate pubecosphere`）；配置 API 密钥（见「LLM 配置」）。

```bash
./run_issue.sh                    # 一键跑一期全流程
DRY_RUN=1 ISSUE=1 ./run_issue.sh  # 只预览 pick 选什么，不下载不生成
```

脚本流程：`capture(可选) → revisit → rank → pick（此刻才下载当期图）→ material → flatten → generate/assemble → baoyu(md→html) → inline_images(base64)`。

常用环境变量（完整见 [run_issue.sh](run_issue.sh) 头部注释）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ISSUE` | `1` | 期号 |
| `WINDOW` | `"10 3"` | 回访/打分相对窗口（天） |
| `WINDOW_FIELD` | `captured_at` | 打分日期基准字段 |
| `MIN_SCORE` | `0.55` | pick 选稿门槛 |
| `RUN_CAPTURE` | `0` | `1` = 先跑每日 capture |
| `DRY_RUN` | `0` | `1` = 只预览 pick |

每期产物在 `output/issue/<期号>/`：`score/`（打分报告）、`pub/` + `manifest.*`（素材与清单）、`material/`（图材）、`upload/`（上传夹）、`weekly/`（周报成品 + `*-base64.html` 微信粘贴用）。不用脚本时各步骤的单条命令见 `CLAUDE.md`「常用命令」。

## LLM 周报生成

- **单模型路径（推荐）**：`python -m llm generate` 用**同一个模型**完成提取 + 写稿（无多模态，图片描述来自评论者配图时的原话）。
- **多模态双模型路径（预留）**：vision 提取 + writer 写稿。

提示词见 `docs/prompts/`（随仓库提交）；方案细节见 `docs/llm-scheme.md`。

### LLM 配置

密钥只走环境变量 / 仓库根 `.env`（**绝不硬编码、不提交**）：

```bash
cp .env.example .env        # 填入 PUBECOSPHERE_LLM_API_KEY=sk-…
```

单模型路径变量：`PUBECOSPHERE_LLM_MODEL`（默认 deepseek-v4-flash）、`PUBECOSPHERE_LLM_MAX_TOKENS`（默认顶满模型上限）。

### 微信手动粘贴

周报 md 经 baoyu 转微信兼容 HTML，再 base64 内联图片后复制进公众号：

```bash
python -m llm.inline_images weekly/1.html   # → weekly/1-base64.html
```

浏览器打开 `*-base64.html` → Ctrl+A → Ctrl+C → 公众号编辑框 Ctrl+V。

## 项目结构

```
PubEcosphere/
├── crawler/            # 爬虫模块（捕获 / 回访 / 导出）
├── scoring/            # 打分系统（两阶段打分 / 选篇打包 / 图材合并）
├── llm/                # LLM 周报生成（提取 / 写稿 / 排版 / base64）
├── run_issue.sh        # 一键流水线脚本
├── images/             # 项目素材
├── data/               # 运行数据（gitignore）
├── output/             # 打分报告 / 素材 / 周报（gitignore）
├── docs/               # 文档
└── .gitignore
```

## 详细文档

| 文档 | 内容 |
| --- | --- |
| `docs/prompts/` | 文本提取 / 多模态提取 / 写稿 三套提示词（随仓库提交） |
| `docs/PubEcosphere.md` | 打分体系 / 完成情况 / 数据来源（本地私有） |
| `docs/llm-scheme.md` | LLM 生成方案细节（本地私有） |
| `CLAUDE.md` | 数据流 / 目录约定 / 常用命令（本地私有） |

## 免责声明

1. 本项目仅供学术诚信研究与交流使用，所有数据来源于 PubPeer 公开评论。
2. 本项目不对因使用本系统造成的任何后果负责。
