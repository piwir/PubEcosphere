# PubAI4S

把 GitHub 上的 AI for Science（AI4S）项目生成可发布的结构化介绍长文：抓取仓库 + 官网 + 本地 codegraph 代码索引，两段式 LLM 流程（提取结构化材料 → 按代码架构写介绍长文），输出 md + html + base64 内联 html。

## 前置

- 父仓库 [PubEcosphere](https://github.com/piwir/PubEcosphere) 已 `pip install -e .`（本包复用其 `llm.*`，零运行时依赖，`pip install -e .` 只装本包本体）。
- LLM 配置继承父仓库根 `.env`（`PUBECOSPHERE_LLM_BASE_URL/API_KEY/MODEL`），本子模块零配置。
- 可选 `GITHUB_TOKEN` 环境变量提升 GitHub API 限额（未认证 60 req/h）。
- 可选 codegraph：`npm install -g @colbymchenry/codegraph`，为阶段A 提供仓库源码的代码结构摘要（纯本地 Rust 内核，不调 LLM）；未安装或失败时自动降级跳过，不影响主流程。

## 用法

```bash
pip install -e .            # 在本子模块目录
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

产物在 `<out-dir>/<owner>-<repo>/` 下（`--out-dir` 默认父仓库根 `output/PubAI4S`，由子模块路径自动推导，子模块本地不落产物）：

- `inputs/` — 抓取的原始输入（meta.txt / readme.md / website.txt / codegraph.txt（可选，codegraph 未装或失败则缺省，自动降级））
- `images.md` + `cover.png` / `img-N.png` / `arch.png` — 图片与清单（徽章/图标/小图自动跳过，失败逐张降级；codegraph 可用时自动生成 `arch.png` 架构思维导图，置于清单首位、不占下载配额）
- `material.md` — 阶段A 提取的结构化材料（末尾含「已下载图片清单」小节）
- `post.md` — 阶段B 发布文案原文（可手工编辑；内容图下一空行 + 斜体图注行，渲染后自动转图下小字并编号）
- `post.html` / `post-base64.html` — 渲染成品，浏览器打开复制即发布

## 克隆注意

父仓库需 `git clone --recurse-submodules`（或 clone 后 `git submodule update --init`）才会带上本子模块。
