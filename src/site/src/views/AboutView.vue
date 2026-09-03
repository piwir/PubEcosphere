<script setup>
import site from '../data/site.json'

const steps = [
  { name: 'capture / revisit', desc: '每日捕获 PubPeer feed，7 天回访提取完整评论线程，SQLite 存储' },
  { name: 'rank / pick', desc: '两阶段打分（廉价粗筛全部 → 短名单深度精筛），每类选 1–2 篇' },
  { name: 'material', desc: '图材合并（每张至多 4 源图、超限拆 _N）+ 结构化素材 md，写回 pub/' },
  { name: 'generate', desc: '单模型 LLM 提取 + 写稿 → 周报草稿，人工审核' },
  { name: 'md2html / 微信', desc: '转微信兼容 HTML、图片 base64 内联 → 手动粘贴发布' },
]
</script>

<template>
  <div class="wrap">
    <section class="about">
      <p class="eyebrow">ABOUT</p>
      <h1 class="page-title">关于</h1>
      <p class="page-sub">PubPeer 学术诚信周报生成管线</p>

      <div class="sec">
        <h2>项目与数据流</h2>
        <p>
          PubEcosphere 抓取 PubPeer 上与作者/打假人「激烈交锋」的论文，
          多维度两阶段打分排序，产出每期图材素材，由 LLM 生成周报，每周三发布到微信公众号。
        </p>
        <div class="flow">
          <div v-for="(s, i) in steps" :key="i">
            <span class="flow-step">{{ s.name }}</span>
            <span class="flow-desc">　{{ s.desc }}</span>
          </div>
        </div>
      </div>

      <div class="sec">
        <h2>技术栈</h2>
        <ul>
          <li>Python 3.10+：爬虫 / 打分 / 图材（纯标准库 + Pillow）</li>
          <li>DeepSeek（OpenAI 兼容 API）：周报提取与写稿</li>
          <li>Vue 3 + Vite：本站（无 UI 库，手写 CSS）</li>
          <li>GitHub Actions：Pages 构建与部署</li>
        </ul>
      </div>

      <div class="sec">
        <h2>数据来源致谢</h2>
        <p class="note">
          中科院分区表 / JCR 影响因子 / CCF 推荐目录 / 国际期刊预警名单派生自
          <a class="sweep-link" href="https://github.com/hitfyd/ShowJCR" target="_blank" rel="noopener">hitfyd/ShowJCR</a>
          （GPLv3），原数据版权归中科院文献情报中心、Clarivate、CCF 等来源方，不属本项目 MIT 代码；
          <a class="sweep-link" href="https://pubpeer.com" target="_blank" rel="noopener">PubPeer</a>
          数据来自其公开 API 与页面。
        </p>
      </div>

      <div class="sec">
        <h2>许可</h2>
        <p>代码 MIT License（见仓库 LICENSE）；数据文件归属见上方致谢。</p>
      </div>
    </section>
  </div>
</template>
