#!/usr/bin/env bash
# 一期周报全流程（数据由长期 cron 爬虫供给，脚本默认不跑爬虫，只管生成）：
# capture/revisit(可选) → rank → pick（此时才下载当期图）→ material → flatten
# → generate(写稿) → assemble(排版) → md2html md→html → inline_images base64。
#
# 环境变量（可覆盖，均有默认值）：
#   ISSUE          期号（默认 1）
#   DB             SQLite 数据库路径（默认 data/pubpeer.db）
#   WINDOW         打分相对窗口 "D1 D2"（默认 "10 3"，引用时展开成两个参数，勿加引号改语义）
#   WINDOW_FIELD   rank 日期基准字段（默认 captured_at，可选 last_commented）
#   MIN_SCORE      pick 选稿门槛（默认 0.55，需用户拍板）
#   RUN_CAPTURE    1 = 先跑每日 capture（默认 0：capture 是每日独立 cron 操作）
#   RUN_REVISIT    1 = 先跑回访 revisit（默认 0：revisit 同样由 cron 供给，脚本只管生成）
#   DRY_RUN        1 = pick 只预览不下载，看完即停（默认 0 全流程）
#   WEEK_START     本期数据收集起始日 YYYY-MM-DD（默认 2026-08-03；每期 +7 天，注入导语）
#   BUN            bun 运行时覆盖（默认经 npx -y bun 启动，见 llm/md2html.py resolve_bun）
#   MD2HTML_MAIN   转换器入口覆盖（默认仓库 vendor/md2html-cli/scripts/render.ts）
#
# 用法：ISSUE=1 ./run_issue.sh    或   DRY_RUN=1 ISSUE=1 ./run_issue.sh
set -euo pipefail
cd "$(dirname "$0")"   # 以仓库根为工作目录

ISSUE="${ISSUE:-1}"
if [[ "$ISSUE" != "-1" ]] && ! [[ "$ISSUE" =~ ^[1-9][0-9]*$ ]]; then
    echo "错误：ISSUE 必须是正整数期号或 -1 测试期，收到：$ISSUE" >&2
    exit 1
fi
DB="${DB:-data/pubpeer.db}"
WINDOW="${WINDOW:-10 3}"
WINDOW_FIELD="${WINDOW_FIELD:-captured_at}"
MIN_SCORE="${MIN_SCORE:-0.55}"
RUN_CAPTURE="${RUN_CAPTURE:-0}"
RUN_REVISIT="${RUN_REVISIT:-0}"
DRY_RUN="${DRY_RUN:-0}"
WEEK_START="${WEEK_START:-2026-08-03}"

OUT_ROOT="output/issue/$ISSUE"
WEEKLY="$OUT_ROOT/weekly"

echo "==> issue $ISSUE · db=$DB · window=$WINDOW($WINDOW_FIELD) · min_score=$MIN_SCORE"

if [[ "$DRY_RUN" == "1" ]]; then
    echo "==> DRY_RUN=1：只预览 pick 结果，不下载图片不生成素材。"
fi

# 0) 每日捕获（可选）
if [[ "$RUN_CAPTURE" == "1" ]]; then
    echo "==> [1/9] capture（每日 feed）"
    python -m crawler.crawl --db "$DB" capture
fi

# 1) 回访（可选）：抓捕获过文章的完整评论（幂等，可断点续跑）——数据由长期 cron 供给，默认不跑
if [[ "$RUN_REVISIT" == "1" ]]; then
    echo "==> [2/9] revisit（--window $WINDOW）"
    python -m crawler.crawl --db "$DB" revisit --window $WINDOW
fi

# 2) 两阶段打分（stage-1 粗筛 → 短名单深度回访 stage-2）
echo "==> [3/9] rank（--issue $ISSUE）"
python -m scoring.pipeline rank --db "$DB" --issue "$ISSUE" --window $WINDOW --window-field "$WINDOW_FIELD"

# 3) 选稿（此刻才为当期被选论文下载评论图片）
if [[ "$DRY_RUN" == "1" ]]; then
    echo "==> [4/9] pick --dry-run（预览，不下载）"
    python -m scoring.pipeline pick --db "$DB" --issue "$ISSUE" --min-score "$MIN_SCORE" --dry-run
    echo "==> 预览结束。确认分数线后去掉 DRY_RUN=1 再跑全流程。"
    exit 0
fi
echo "==> [4/9] pick（下载当期图 + 写 manifest）"
python -m scoring.pipeline pick --db "$DB" --issue "$ISSUE" --min-score "$MIN_SCORE"

# 4) 图材整理：合并图（first/author/sleuth，超 4 张源图拆 _N）
echo "==> [5/9] material（合并图 → $OUT_ROOT/material/）"
python -m scoring.material --pub-dir "$OUT_ROOT/pub" --out "$OUT_ROOT/material"

# 5) 摊平上传文件夹（扁平 <pid>_<kind>.png）
echo "==> [6/9] flatten（→ $OUT_ROOT/upload/）"
python -m llm flatten --material-dir "$OUT_ROOT/material" --upload-dir "$OUT_ROOT/upload" \
    --manifest "$OUT_ROOT/manifest.json"

# 6) 单模型提取 + 写稿 + 排版成品（--assemble 顺带跑 assemble）
#    WEEK_START 透传给 generate：头部 `数据收集：M.DD–M.DD` → 导语第一句
export WEEK_START
echo "==> [7/9] generate --assemble（提取+写稿+排版 → $WEEKLY/）"
python -m llm generate --material-dir "$OUT_ROOT/material" --weekly-dir "$WEEKLY" \
    --issue "$ISSUE" --assemble

# 7) md → 微信兼容 HTML（--keep-title 保留主标题 `# PubPeer 周报 · issue N`）
#    转换器为仓库内 vendor（vendor/md2html-cli），bun 默认经 npx -y bun 启动，
#    不再依赖外部 skill ~/.claude/skills/baoyu-markdown-to-html。
echo "==> [8/9] md2html md → html"
python -m llm md2html "$WEEKLY/$ISSUE.md" --theme default --keep-title

# 7.3) 周报 HTML 后处理（确定性、幂等）：
#      a) 数学上下标（LaTeX 残记 `^()`/`_x` → <sup>/<sub>，微信不认 KaTeX）；
#      b) 卡片页脚「PubPeer 讨论 + DOI」blockquote 的 <p> 注入与英文标题一致的
#         小字紧排（font-size calc(Npx*0.85)、line-height 1.3）。
#      c) 修复 blockquote 内被转换器剥成纯文本的 GitHub 链接（包回 <a href>）。
#      b 不能用 CSS：页脚 blockquote 在 `**现状**` 段落后、无相邻选择器可命中，juice 不支持 :has()
echo "==> [8.3/9] polish_html（上下标 + 页脚小字 + GitHub 链接修复）"
python -m llm.polish_html "$WEEKLY/$ISSUE.html"

# 8) 图片 base64 内联（微信手动粘贴专用；同时清掉转换器 data-local-path 绝对路径残留）
echo "==> [9/9] inline_images（→ $WEEKLY/$ISSUE-base64.html）"
python -m llm.inline_images "$WEEKLY/$ISSUE.html"

echo
echo "==> 完成。产物："
echo "    素材清单    $OUT_ROOT/manifest.md"
echo "    图材        $OUT_ROOT/material/"
echo "    上传文件夹  $OUT_ROOT/upload/"
echo "    周报成品    $WEEKLY/$ISSUE.md"
echo "    微信粘贴用  $WEEKLY/$ISSUE-base64.html"
echo "    （浏览器双击打开 base64 html → Ctrl+A → Ctrl+C → 公众号编辑框 Ctrl+V）"
