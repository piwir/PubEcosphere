# PubAI4S（`modules/ai4s`）

把 GitHub 上的 AI for Science（AI4S）项目生成可发布的结构化介绍长文：抓取仓库 + 官网 + 本地
codegraph 代码索引，两段式 LLM 流程（提取结构化材料 → 按代码架构写介绍长文），输出
md + html + base64 内联 html。

这是主仓库 [PubEcosphere](https://github.com/piwir/PubEcosphere) 的一个模块，**不是独立仓库**：
与周报流水线一起由仓库根的 `pyproject.toml` 打包，一次 `pip install -e .` 装齐，
共用 `src/llm/`（LLM 客户端与渲染）与 `src/core/`（通用小工具）。

## 前置

- 仓库根已 `pip install -e .`（本模块随之装好，无需单独安装）。
- LLM 配置继承仓库根 `.env`（`PUBECOSPHERE_LLM_BASE_URL/API_KEY/MODEL`），本模块零配置。
- 可选 `GITHUB_TOKEN` 环境变量提升 GitHub API 限额（未认证 60 req/h）。
- 可选 codegraph：`npm install -g @colbymchenry/codegraph`，为阶段A 提供仓库源码的代码结构摘要
  （纯本地 Rust 内核，不调 LLM）；未安装或失败时自动降级跳过，不影响主流程。

## 用法

```bash
python -m pubai4s check     # 校验提示词八节齐全

python -m pubai4s run https://github.com/<owner>/<repo>        # 全流程（抓取 → 提取 → 写稿 → 渲染）
python -m pubai4s run git@github.com:<owner>/<repo>.git         # ssh 写法
python -m pubai4s run <owner>/<repo>                            # 简写
python -m pubai4s run <repo_url> --model <model>                # 覆盖模型
python -m pubai4s run <repo_url> --dry-run                      # 只抓取预览，不调 LLM

python -m pubai4s fetch <repo_url>    # 只抓取仓库/官网 + 下载图片（不调 LLM）
python -m pubai4s archmap <out_dir>   # 从 <out_dir>/inputs/codegraph.txt 生成 arch.png 架构思维导图
python -m pubai4s render <out_dir>    # 把 <out_dir>/post.md 渲染成 html + base64 html
```

产物在 `<out-dir>/<owner>-<repo>/` 下（`--out-dir` 默认仓库根 `output/PubAI4S`，模块内不落产物）：

- `inputs/` — 抓取的原始输入（meta.txt / readme.md / website.txt / codegraph.txt（可选，codegraph 未装或失败则缺省，自动降级））
- `images.md` + `cover.png` / `img-N.png` / `arch.png` — 图片与清单（徽章/图标/小图自动跳过，失败逐张降级；codegraph 可用时自动生成 `arch.png` 架构思维导图，置于清单首位、不占下载配额）
- `material.md` — 阶段A 提取的结构化材料（末尾含「已下载图片清单」小节）
- `post.md` — 阶段B 发布文案原文（可手工编辑；内容图下一空行 + 斜体图注行，渲染后自动转图下小字并编号）
- `post.html` / `post-base64.html` — 渲染成品，浏览器打开复制即发布

## 模块内容

```
modules/ai4s/
├── pubai4s/           # 代码（archmap/codegraph/extract/generate/github/render/site/prompts）
└── prompts/           # prompt_extract.md（阶段A）+ prompt_writer.md（阶段B）
```

## 归档到官网

```bash
python -m sitejson sync-ai4s --dir output/PubAI4S/<owner>-<repo>
```

只写 `src/site/src/data/site.json` 的 `ai4s.posts`（幂等、按 repo 去重）；公众号链接发布后
在 `ai4s.posts[].url` 补上再跑一次即可。
