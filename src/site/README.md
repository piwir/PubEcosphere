# PubEcosphere 站点（GitHub Pages）

Vue 3 + Vite + vue-router（hash 路由），无 UI 库、手写 CSS。构建产物由
`.github/workflows/pages.yml` 部署到 https://piwir.github.io/PubEcosphere/。

## 每期更新
每期生成结束后，流水线会自动跑 `python -m sitejson sync --issue N` 写
`src/data/site.json`：`currentIssue` / `issueDate` / `window` + `issues[]` 头部
一条 `{ "issue": N, "date": "YYYY-MM-DD", "wechatUrl": "", "summary": "PubPeer 周报 · issue N" }`。

公众号发布后：

1. 把链接填到 `issues[]` 里第 N 期的 `wechatUrl`（是链接的唯一来源）；
2. 再跑一次 `python -m sitejson sync --issue N`（同步首页 CTA `wechat.url` / `wechat.label`）；
3. push，GitHub Actions 自动重新部署。

链接未发布时 `wechat.url` 为空，首页 CTA 显示不可点的「第 N 期周报即将发布」。
`python -m sitejson --selftest` 可离线自检（派生规则与已发布 1–5 期回归）；
`--dry-run` 只看将发生的变化、不写盘。窗口 = `WEEK_START+(N-1)*7` 起 7 天，
发布日 = 窗口起始 +9 天（周三）。

## AI4S 页更新

推文发布后编辑 `src/data/site.json` 的 `ai4s` 字段：`posts` 数组头部加一条
`{ "no": N, "repo": "owner/repo", "name": "项目名", "date": "YYYY-MM-DD", "summary": "一句话摘要", "url": "公众号链接" }`；
未发布的推文 `url` 留空（页面显示「即将发布」）。

**隐私**：site.json 随仓库公开——只放期号/日期/窗口/链接/中性摘要，
不放周报正文、pubpeer id、打假人姓名。归档摘要为固定文案 `PubPeer 周报 · issue N`，
不要写成含内容细节的描写。

## 本地开发

```bash
npm install
npm run dev       # http://localhost:5173/PubEcosphere/
npm run build     # 产物 dist/（gitignored）
npm run preview   # 本地预览构建产物
```

## 换图

站点图片在 `public/`（icon.png 顶栏 logo / intro.jpg 首页头图 / QR.png 公众号二维码），
源图在仓库根 `images/`。**换图时两处同步**（改 `images/` 后重新复制到 `public/`；
头图压到宽 ~1760px 的 JPEG 以控制体积，favicon/logo 压到 120px）。

## 设计说明

「卷宗 + 荧光笔」：纸白底 + 墨色字 + 等宽元数据；首页期号居中（全页唯一主角），
唯一动效是荧光笔扫过期号（PubPeer 评论者高亮疑点的标志性动作），
`prefers-reduced-motion` 时静态；公众号二维码在页脚联系区（点击打开大图）；
全站链接统一墨色 + hover 荧光笔下划线（蓝色 `--link` 只用于键盘焦点环）；
流水线为单行等宽工序（抓取 → 打分 → 生成，无框）。
颜色只取 `src/styles/main.css` 顶部六色 token。
