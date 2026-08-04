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
│   └── report.py       # 周报生成：按评论时间过滤 [now-10d, now-3d] 输出 Markdown
├── data/               # 运行数据
├── output/             # 生成的周报 Markdown
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


## 免责声明

1. 本项目仅供学术诚信研究与交流使用，所有数据来源于 PubPeer 公开评论。
2. 本项目不对因使用本系统造成的任何后果负责。
