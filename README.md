# PubEcosphere

> 一个自动化爬取 PubPeer 平台文章，关注作者与学术打假人激烈交锋，生成周报的推送系统

基于 PubPeer(pubpeer.com) 公开评论数据，抓取存在**作者与打假人激烈交锋**的论文，通过多维度打分排序，由大模型生成微信服务号周报，经人工审核后推送。

## 项目简介

学术打假是维护科研诚信的重要防线。PubPeer 作为全球最大的学术评论平台之一，其评论区常常上演作者申辩与质疑者对线的精彩交锋。本系统旨在：

- **自动化监控** PubPeer 上高关注度的论文评论动态
- **智能打分** 筛选出值得报道的「激烈交锋」案例
- **周报化** 由大模型润色成可读性强的公众号推文
- **合规推送** 经人工审核后通过微信服务号发布


## 核心工作流

1. **服务号申请** — 完成微信服务号注册，获取推送通道
2. **爬虫 + 结构化** — 爬取 PubPeer 文章评论数据，整理为 Markdown 结构化数据
3. **打分体系** — 对候选文章多维度打分，筛选「激烈交锋」的高价值案例
4. **图片处理** — 将多张截图/图片合成为一张，嵌入推文
5. **大模型生成** — 接入大模型，识别冲突点并生成推文草稿
6. **审核推送** — 人工审核后通过服务号群发

## 项目结构

```
PubEcosphere/
├── images/             # 项目素材
├── crawler/            # 爬虫模块
│   ├── client.py       # HTTP 客户端：浏览器 UA、限速、退避重试、接口 URL
│   ├── dates.py        # 日期解析：feed 格式 / ISO 格式归一化为 UTC
│   ├── parser.py       # 解析 feed 条目 + 文章页内嵌 JSON
│   ├── store.py        # SQLite 存储：捕获记录 / 文章 / 评论（幂等 upsert）
│   ├── crawl.py        # 主入口：capture 捕获 feed + revisit 延迟回访评论
│   ├── export.py       # 导出：回访数据整理为 md + 评论图片下载本地化
├── scoring/            # 打分系统：学科分类 + 两阶段打分选优 + 周报素材打包
│   ├── pipeline.py     # CLI：coverage/ rank/ pick（
│   ├── cas.py          # 中科院分区表 2025：大类/小类/分区/Top
│   ├── jcr.py          # JCR 2025 影响因子
│   ├── ccf.py          # CCF 推荐目录兜底 + 国际期刊预警名单
│   ├── enrich.py       # CrossRef DOI 解析 + PubPeer v3 评论摘要
│   ├── score.py        # stage-1 廉价粗筛 + stage-2 深度「激烈交互」打分
│   ├── signals.py      # 从评论提取撤稿/证据/多轮交锋/时间跨度等信号
│   ├── revisit.py      # 短名单深度回访（复用 crawler）
│   ├── issue.py        # 每类选 → 收集 md+图片到 output/issue/<期号>/ → 标记已发布
│   ├── report.py       # 输出 JSON + Markdown 到 output/score/<日期>/
│   └── config.py       # 权重/基线/打假人名单/分类粒度/每期篇数，全部可调
├── data/               # 运行数据（含 gitignore 的 ShowJCR 分类/IF 数据）
├── output/             # 生成的周报 Markdown + 打分报告
├── environment.yml     # 服务器 conda 环境配置
├── README.md
└── .gitignore
```

## 爬虫：捕获 + 延迟回访

PubPeer 的公开接口 `/api/recent/from/{0..400}` 只能看到**最近约 3 天**的评论动态，无法直接回溯到周报窗口。系统采用「**捕获 → 延迟回访**」设计：

1. **每日捕获（capture）**：拉取 `/api/recent` 全部约 400 条，记录 `pubpeer_id` 与元数据到 SQLite。
2. **延迟回访（revisit）**：7 天后回访这些文章页，提取完整评论线程——此时当时的动态恰好落在 **[now-10d, now-3d] 周报窗口**内。
3. **导出存档（export）**：把回访所得的文章+评论整理成 Markdown，并**把评论里的图片下载到本地**。

所有请求带浏览器 UA、限速 1.5s、对瞬时错误退避重试；捕获与评论均为幂等 upsert，可断点续跑。

bash
# 每日捕获（服务器 cron）：记录 feed 中的 pubpeer_id
python -m crawler.crawl --db data/pubpeer.db capture

# 回访（>=7 天后）：抓捕获过的文章页，提取评论（--limit 可分批续跑）
python -m crawler.crawl --db data/pubpeer.db revisit --limit 50

# 导出：每篇文章一个 md（output/pub/<pubpeer_id>.md），评论图片下载到 <pubpeer_id>_files/
python -m crawler.export --db data/pubpeer.db --output output



```
output/pub/
├── <pubpeer_id>.md          # 一篇文章一份：元数据 + 完整评论线程
└── <pubpeer_id>_files/      # 该文评论引用的图片
```

### 服务器部署

1. 在常开服务器上 clone 仓库：`git clone <repo-url>`
2. 配置环境：`conda env create -f environment.yml && conda activate pubecosphere`
3. 每日用 cron 运行捕获：`0 4 * * * cd /path/to/PubEcosphere && python -m crawler.crawl --db data/pubpeer.db capture >> logs/crawl.log 2>&1`
4. 每周回访并导出存档：
   `python -m crawler.crawl --db data/pubpeer.db revisit` → `python -m crawler.export --db data/pubpeer.db --output output`

## 打分系统：学科分类 + 两阶段选优

按 HelloGitHub 模式，把捕获文章按**学科（中科院分区表大类/小类）**分组，每类挑出「激烈交锋」高分候选，供 AI 生成周报。

```bash
# ① 期刊覆盖率报告（分类/IF/CCF/预警的命中情况，不联网不评分）
python -m scoring.pipeline coverage --db data/pubpeer.db

# ② 两阶段打分选优：
#    stage-1 全部用廉价数据（捕获 + CrossRef/v3 富集）粗筛
#    stage-2 每类高分短名单深度回访，用真实评论算「激烈交互」
python -m scoring.pipeline rank --db data/pubpeer.db --stage1-only   # 只粗筛（快）
python -m scoring.pipeline rank --db data/pubpeer.db                 # 完整两阶段

# ③ 每期素材打包：rank 之后按类别选篇（每类至多 2 篇，小类 1 篇），
#    把文章的 md + 评论图片收到 output/issue/<期号>/，并在数据库标记已发布
python -m scoring.pipeline pick --db data/pubpeer.db --issue=1 --dry-run   # 先看选什么
python -m scoring.pipeline pick --db data/pubpeer.db --issue=1             # 正式打包（测试用 --issue=-1）
```

输出到 `output/score/<日期>/`：每分类的候选表（评分明细可审计）、全量分数 JSON、覆盖率报告。权重/打假人名单/每类篇数等见 `scoring/config.py`，均可调。

**每期素材**：`output/issue/<期号>/` 下 `manifest.md`（类别索引 + 分数 + 链接）、`manifest.json`、`pub/<pubpeer_id>.md`（完整评论线程）+ `<pubpeer_id>_files/`（评论图片本地化）。已标记发布（`published` 表）的文章，后续 `rank` 与 `pick` 默认不再考虑（`rank --include-published` 可强制纳入）。

**数据来源**：中科院分区表 2025 / JCR 影响因子 / CCF 目录 / 预警名单来自 [hitfyd/ShowJCR](https://github.com/hitfyd/ShowJCR) 仓库（`data/` 下，gitignore 不提交）；DOI 解析走 CrossRef；评论摘要走 PubPeer v3 API。

## 免责声明

1. 本项目仅供学术诚信研究与交流使用，所有数据来源于 PubPeer 公开评论。
2. 本项目不对因使用本系统造成的任何后果负责。
