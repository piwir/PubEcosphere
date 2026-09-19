<script setup>
import { RouterLink } from 'vue-router'
import site from '../data/site.json'

const base = import.meta.env.BASE_URL
const no = String(site.currentIssue).padStart(2, '0')
</script>

<template>
  <div class="wrap">
    <section class="hero">
      <div class="hero-main">
        <p class="eyebrow">{{ site.tagline }}</p>

        <div class="issue" aria-label="当前期号">
          <span class="issue-word">第</span>
          <span class="issue-num">{{ site.currentIssue }}</span>
          <span class="issue-word">期</span>
        </div>

        <p class="meta">
          NO.{{ no }} · <b>{{ site.issueDate }}</b> · 素材窗口 {{ site.window }}
        </p>

        <a v-if="site.wechat.url" class="cta" :href="site.wechat.url" target="_blank" rel="noopener">
          {{ site.wechat.label }} →
        </a>
        <span v-else class="cta cta-pending">{{ site.wechat.label }}</span>

        <p class="sublinks">
          <a class="sweep-link" :href="site.github" target="_blank" rel="noopener">GitHub</a>
          <RouterLink class="sweep-link" to="/archive">往期归档</RouterLink>
          <RouterLink class="sweep-link" to="/about">关于</RouterLink>
        </p>

      </div>

      <figure class="exhibit">
        <img :src="base + 'intro.jpg'" alt="PubEcosphere 周报头图">
        <figcaption>每周从 PubPeer 公开评论中精选围绕原始数据、图表与方法展开激烈讨论的论文</figcaption>
      </figure>

      <p class="pipeline" aria-label="流水线">
        <span class="step">抓取</span>
        <span class="step-arrow" aria-hidden="true">→</span>
        <span class="step">打分</span>
        <span class="step-arrow" aria-hidden="true">→</span>
        <span class="step">生成</span>
      </p>
      <p class="mcp-note">MCP server 可挂载到个人 agent</p>
    </section>
  </div>
</template>
