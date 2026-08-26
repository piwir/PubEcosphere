#!/usr/bin/env bash
# 一期周报全流程（数据由长期 cron 爬虫供给，脚本默认不跑爬虫，只管生成）：
# capture/revisit(可选) → rank → pick（此时才下载当期图）→ material
# → generate(写稿) → assemble(排版) → md2html md→html → inline_images base64。
#
# 环境变量（可覆盖，均有默认值）：
#   ISSUE          期号（默认 1；正整数期号自动推算绝对打分窗口，-1 测试期用相对窗口）
#   DB             SQLite 数据库路径（默认 data/pubpeer.db）
#   WINDOW         打分相对窗口 "D1 D2"（默认 "10 3"，仅 ISSUE=-1 或手工 CLI 用；正整数期号走绝对窗口）
#   WINDOW_FIELD   rank 日期基准字段（默认 captured_at，可选 last_commented）
#   MIN_SCORE      pick 选稿门槛（默认 0.50，用户已拍板；每周分数分布不同，篇数配合 MAX_PICKS 上限浮动）
#   MAX_PICKS      pick 每期入选总数上限（默认 10；每门类至少 1 篇、尽量覆盖 ≥7 个门类，剩余名额按分补第 2 篇，0 = 不限）
#   RUN_CAPTURE    1 = 先跑每日 capture（默认 0：capture 是每日独立 cron 操作）
#   RUN_REVISIT    1 = 先跑回访 revisit（默认 0：revisit 同样由 cron 供给，脚本只管生成）
#   DRY_RUN        1 = pick 只预览不下载，看完即停（默认 0 全流程）
#   WEEK_START     第 1 期数据收集起始日 YYYY-MM-DD（默认 2026-08-03；单一锚点：
#                  正整数期号的打分窗口 = [WEEK_START+(期号-1)*7, +7 天)，与导语标签同算法）
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
MIN_SCORE="${MIN_SCORE:-0.50}"
RUN_CAPTURE="${RUN_CAPTURE:-0}"
RUN_REVISIT="${RUN_REVISIT:-0}"
DRY_RUN="${DRY_RUN:-0}"
WEEK_START="${WEEK_START:-2026-08-03}"
MAX_PICKS="${MAX_PICKS:-10}"

OUT_ROOT="output/issue/$ISSUE"
WEEKLY="$OUT_ROOT/weekly"

# 打分窗口：正整数期号 → 绝对窗口 [WEEK_START+(期号-1)*7, +7 天)（与导语标签同算法，
# 与运行日无关）；-1 测试期 → 相对窗口 $WINDOW。
if [[ "$ISSUE" == "-1" ]]; then
    WIN_ARGS=(--window $WINDOW)
    echo "==> issue $ISSUE · db=$DB · window=$WINDOW(相对,$WINDOW_FIELD) · min_score=$MIN_SCORE"
else
    if ! WEEK_FROM=$(date -d "$WEEK_START + $(( (ISSUE - 1) * 7 )) days" +%F 2>/dev/null); then
        echo "错误：WEEK_START 不是合法日期（应为 ISO yyyy-mm-dd）：$WEEK_START" >&2
        exit 1
    fi
    WEEK_TO=$(date -d "$WEEK_FROM + 7 days" +%F)
    WIN_ARGS=(--window-dates "$WEEK_FROM" "$WEEK_TO")
    echo "==> issue $ISSUE · db=$DB · window-dates=$WEEK_FROM..$WEEK_TO($WINDOW_FIELD) · min_score=$MIN_SCORE · max_picks=$MAX_PICKS"
fi

if [[ "$DRY_RUN" == "1" ]]; then
    echo "==> DRY_RUN=1：只预览 pick 结果，不下载图片不生成素材。"
fi

# 0) 每日捕获（可选）
if [[ "$RUN_CAPTURE" == "1" ]]; then
    echo "==> [1/8] capture（每日 feed）"
    python -m crawler.crawl --db "$DB" capture
fi

# 1) 回访（可选）：抓捕获过文章的完整评论（幂等，可断点续跑）——数据由长期 cron 供给，默认不跑
if [[ "$RUN_REVISIT" == "1" ]]; then
    echo "==> [2/8] revisit（${WIN_ARGS[*]}）"
    python -m crawler.crawl --db "$DB" revisit "${WIN_ARGS[@]}"
fi

# 2) 两阶段打分（stage-1 粗筛 → 短名单深度回访 stage-2）
echo "==> [3/8] rank（--issue $ISSUE，${WIN_ARGS[*]}）"
python -m scoring.pipeline rank --db "$DB" --issue "$ISSUE" "${WIN_ARGS[@]}" --window-field "$WINDOW_FIELD"

# 3) 选稿（此刻才为当期被选论文下载评论图片）
if [[ "$DRY_RUN" == "1" ]]; then
    echo "==> [4/8] pick --dry-run（预览，不下载）"
    python -m scoring.pipeline pick --db "$DB" --issue "$ISSUE" --min-score "$MIN_SCORE" \
        --max-total "$MAX_PICKS" --dry-run
    echo "==> 预览结束。确认分数线后去掉 DRY_RUN=1 再跑全流程。"
    exit 0
fi
echo "==> [4/8] pick（下载当期图 + 写 manifest）"
python -m scoring.pipeline pick --db "$DB" --issue "$ISSUE" --min-score "$MIN_SCORE" \
    --max-total "$MAX_PICKS"

# 4) 图材整理：合并图（first/author/sleuth，超 4 张源图拆 _N）+ 结构化 md 写回 pub/<pid>_files/
echo "==> [5/8] material（合并图写回 $OUT_ROOT/pub/）"
python -m scoring.material --pub-dir "$OUT_ROOT/pub"

# 5) 单模型提取 + 写稿 + 排版成品（--assemble 顺带跑 assemble）
#    WEEK_START 透传给 generate：头部 `数据收集：M.DD–M.DD` → 导语第一句
export WEEK_START
echo "==> [6/8] generate --assemble（提取+写稿+排版 → $WEEKLY/）"
python -m llm generate --material-dir "$OUT_ROOT/pub" --weekly-dir "$WEEKLY" \
    --issue "$ISSUE" --assemble

# 6) md → 微信兼容 HTML（--keep-title 保留主标题 `# PubPeer 周报 · issue N`）
#    转换器为仓库内 vendor（vendor/md2html-cli），bun 默认经 npx -y bun 启动，
#    不依赖任何外部 skill。
echo "==> [7/8] md2html md → html"
python -m llm md2html "$WEEKLY/$ISSUE.md" --theme default --keep-title

# 6.3) 周报 HTML 后处理（确定性、幂等）：
#      a) 数学上下标（LaTeX 残记 `^()`/`_x` → <sup>/<sub>，微信不认 KaTeX）；
#      b) 卡片页脚「PubPeer 讨论 + DOI」blockquote 的 <p> 注入与英文标题一致的
#         小字紧排（font-size calc(Npx*0.85)、line-height 1.3）。
#      c) 修复 blockquote 内被转换器剥成纯文本的 GitHub 链接（包回 <a href>）。
#      b 不能用 CSS：页脚 blockquote 在 `**现状**` 段落后、无相邻选择器可命中，juice 不支持 :has()
echo "==> [7.3/8] polish_html（上下标 + 页脚小字 + GitHub 链接修复）"
python -m llm.polish_html "$WEEKLY/$ISSUE.html"

# 7) 图片 base64 内联（微信手动粘贴专用；同时清掉转换器 data-local-path 绝对路径残留）
echo "==> [8/8] inline_images（→ $WEEKLY/$ISSUE-base64.html）"
python -m llm.inline_images "$WEEKLY/$ISSUE.html"

echo
echo "==> 完成。产物："
echo "    素材清单    $OUT_ROOT/manifest.md"
echo "    图材        $OUT_ROOT/pub/（合并图 + 结构化素材 md，源图已删）"
echo "    周报成品    $WEEKLY/$ISSUE.md"
echo "    微信粘贴用  $WEEKLY/$ISSUE-base64.html"
echo "    （浏览器双击打开 base64 html → Ctrl+A → Ctrl+C → 公众号编辑框 Ctrl+V）"
